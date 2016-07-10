#
# Copyright 2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

import errno
import logging
import time
from . import uuid
from . import stats
from vdsm import cpuarch
from vdsm import hooks
from vdsm import utils
from vdsm import metrics
from vdsm.config import config
from vdsm.define import Kbytes, Mbytes
from vdsm.virt import vmstatus

haClient = None
try:
    import ovirt_hosted_engine_ha.client.client as haClient
except ImportError:
    pass


def get_stats(cif, sample):
    """
    Retreive host internal statistics
    """
    hooks.before_get_stats()
    ret = {}

    first_sample, last_sample, _ = sample
    decStats = stats.produce(first_sample, last_sample)

    if cif.irs:
        decStats['storageDomains'] = cif.irs.repoStats()
        del decStats['storageDomains']['status']
    else:
        decStats['storageDomains'] = {}

    for var in decStats:
        ret[var] = utils.convertToStr(decStats[var])

    ret['memAvailable'] = _memAvailable(cif) / Mbytes
    ret['memCommitted'] = _memCommitted(cif) / Mbytes
    ret['memFree'] = _memFree() / Mbytes
    ret['swapTotal'], ret['swapFree'] = _readSwapTotalFree()
    (ret['vmCount'], ret['vmActive'], ret['vmMigrating'],
     ret['incomingVmMigrations'], ret['outgoingVmMigrations']) = \
        _countVms(cif)
    (tm_year, tm_mon, tm_day, tm_hour, tm_min, tm_sec,
        dummy, dummy, dummy) = time.gmtime(time.time())
    ret['dateTime'] = '%02d-%02d-%02dT%02d:%02d:%02d GMT' % (
        tm_year, tm_mon, tm_day, tm_hour, tm_min, tm_sec)
    ret['momStatus'] = cif.mom.getStatus()
    ret.update(cif.mom.getKsmStats())
    ret['netConfigDirty'] = str(cif._netConfigDirty)
    ret['haStats'] = _getHaInfo()
    if ret['haStats']['configured']:
        # For backwards compatibility, will be removed in the future
        ret['haScore'] = ret['haStats']['score']

    ret = hooks.after_get_stats(ret)
    return ret


def report_stats(hoststats):
    prefix = "hosts." + uuid()
    report = {}

    try:
        for dom in hoststats['storageDomains']:
            storage_prefix = prefix + '.storage.' + dom
            dom_info = hoststats['storageDomains'][dom]
            report[storage_prefix + '.delay'] = dom_info['delay']
            report[storage_prefix + '.last_check'] = dom_info['lastCheck']

        report[prefix + '.memory.available'] = hoststats['memAvailable']
        report[prefix + '.memory.committed'] = hoststats['memCommitted']
        report[prefix + '.memory.free_mb'] = hoststats['memFree']
        report[prefix + '.memory.usage_percent'] = hoststats['memUsed']
        report[prefix + '.memory.anon_huge_pages'] = hoststats['anonHugePages']

        report[prefix + '.swap.total_mb'] = hoststats['swapTotal']
        report[prefix + '.swap.free_mb'] = hoststats['swapFree']

        report[prefix + '.vms.active'] = hoststats['vmActive']
        report[prefix + '.vms.total'] = hoststats['vmCount']

        report[prefix + '.cpu.load'] = hoststats['cpuLoad']
        report[prefix + '.cpu.user'] = hoststats['cpuUser']
        report[prefix + '.cpu.sys'] = hoststats['cpuSys']
        report[prefix + '.cpu.idle'] = hoststats['cpuIdle']
        report[prefix + '.cpu.sys_vdsmd'] = hoststats['cpuSysVdsmd']
        report[prefix + '.cpu.user_vdsmd'] = hoststats['cpuUserVdsmd']
        report[prefix + '.cpu.ksm_pages'] = hoststats['ksmPages']
        report[prefix + '.cpu.ksm_cpu_precent'] = hoststats['ksmCpu']

        if hoststats['haStats']['configured']:
            report[prefix + '.ha_score'] = hoststats['haScore']

        report[prefix + '.elapsed_time'] = hoststats['elapsedTime']

        if 'network' in hoststats:
            for interface in hoststats['network']:
                if_info = hoststats['network'][interface]
                net_prefix = prefix + '.network_interfaces.' + interface
                report[net_prefix + '.speed'] = if_info['speed']
                report[net_prefix + '.rx_rate'] = if_info['rxRate']
                report[net_prefix + '.tx_rate'] = if_info['txRate']
                report[net_prefix + '.rx_errors'] = if_info['rxErrors']
                report[net_prefix + '.tx_errors'] = if_info['txErrors']
                report[net_prefix + '.rx_dropped'] = if_info['rxDropped']
                report[net_prefix + '.tx_dropped'] = if_info['txDropped']
                report[net_prefix + '.rx'] = if_info['rx']
                report[net_prefix + '.tx'] = if_info['tx']

        metrics.send(report)
    except KeyError:
        logging.exception('Report host stats failed')


def _readSwapTotalFree():
    meminfo = utils.readMemInfo()
    return meminfo['SwapTotal'] / 1024, meminfo['SwapFree'] / 1024


# take a rough estimate on how much free mem is available for new vm
# memTotal = memFree + memCached + mem_used_by_non_qemu + resident  .
# simply returning (memFree + memCached) is not good enough, as the
# resident set size of qemu processes may grow - up to  memCommitted.
# Thus, we deduct the growth potential of qemu processes, which is
# (memCommitted - resident)

def _memCommitted(cif):
    """
    Return the amount of memory (Mb) committed for VMs
    """
    committed = 0
    for v in cif.vmContainer.values():
        committed += v.memCommitted
    return committed


def _memAvailable(cif):
    """
    Return an approximation of available memory for new VMs.
    """
    memCommitted = _memCommitted(cif)
    resident = 0
    for v in cif.vmContainer.values():
        if v.conf['pid'] == '0':
            continue
        try:
            with open('/proc/' + v.conf['pid'] + '/statm') as statmfile:
                resident += int(statmfile.read().split()[1])
        except:
            pass
    resident *= cpuarch.PAGE_SIZE_BYTES
    meminfo = utils.readMemInfo()
    freeOrCached = (meminfo['MemFree'] +
                    meminfo['Cached'] + meminfo['Buffers']) * Kbytes
    return freeOrCached + resident - memCommitted - \
        config.getint('vars', 'host_mem_reserve') * Mbytes


def _memFree():
    """
    Return the actual free mem on host.
    """
    meminfo = utils.readMemInfo()
    return (meminfo['MemFree'] +
            meminfo['Cached'] + meminfo['Buffers']) * Kbytes


def _countVms(cif):
    count = active = incoming = outgoing = 0
    for vmId, v in cif.vmContainer.items():
        try:
            count += 1
            status = v.lastStatus
            if status == vmstatus.UP:
                active += 1
            elif status == vmstatus.MIGRATION_DESTINATION:
                incoming += 1
            elif status == vmstatus.MIGRATION_SOURCE:
                outgoing += 1
        except:
            logging.error(vmId + ': Lost connection to VM')
    return count, active, incoming + outgoing, incoming, outgoing


def _getHaInfo():
    """
    Return Hosted Engine HA information for this host.
    """
    i = {
        'configured': False,
        'active': False,
        'score': 0,
        'globalMaintenance': False,
        'localMaintenance': False,
    }
    if haClient:
        try:
            instance = haClient.HAClient()
            host_id = instance.get_local_host_id()

            # If a host id is available, consider HA configured
            if host_id:
                i['configured'] = True
            else:
                return i

            stats = instance.get_all_stats()
            if 0 in stats:
                i['globalMaintenance'] = stats[0].get(
                    haClient.HAClient.GlobalMdFlags.MAINTENANCE,
                    False)
            if host_id in stats:
                i['active'] = stats[host_id]['live-data']
                i['score'] = stats[host_id]['score']
                i['localMaintenance'] = stats[host_id]['maintenance']
        except IOError as ex:
            if ex.errno == errno.ENOENT:
                logging.error(
                    ("failed to retrieve Hosted Engine HA score '{0}'"
                        "Is the Hosted Engine setup finished?")
                    .format(str(ex))
                )
            else:
                logging.exception(
                    "failed to retrieve Hosted Engine HA score"
                )
        except Exception:
            logging.exception("failed to retrieve Hosted Engine HA info")
    return i
