# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import errno
import logging
import time
from . import stats
from vdsm import utils
from vdsm import metrics
from vdsm.common import hooks
from vdsm.common.units import KiB, MiB
from vdsm.virt import vmstatus

haClient = None
try:
    import ovirt_hosted_engine_ha.client.client as haClient
except ImportError:
    pass


def get_stats(cif, sample, multipath=False):
    """
    Retrieve host internal statistics.
    """
    hooks.before_get_stats()
    ret = {}

    first_sample, last_sample, _ = sample
    decStats = stats.produce(first_sample, last_sample)

    if cif.irs:
        decStats['storageDomains'] = cif.irs.repoStats()
        del decStats['storageDomains']['status']
        if multipath:
            decStats['multipathHealth'] = cif.irs.multipath_health()
            del decStats['multipathHealth']['status']
    else:
        decStats['storageDomains'] = {}

    for var in decStats:
        ret[var] = utils.convertToStr(decStats[var])

    ret['memFree'] = _memFree() // MiB
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


def send_metrics(hoststats):
    prefix = "hosts"
    data = {}

    try:
        for dom in hoststats['storageDomains']:
            storage_prefix = prefix + '.storage.' + dom
            dom_info = hoststats['storageDomains'][dom]
            data[storage_prefix + '.delay'] = dom_info['delay']
            data[storage_prefix + '.last_check'] = dom_info['lastCheck']

        metrics.send(data)
    except KeyError:
        logging.exception('Host metrics collection failed')


def _readSwapTotalFree():
    meminfo = utils.readMemInfo()
    return meminfo['SwapTotal'] // 1024, meminfo['SwapFree'] // 1024


def _memFree():
    """
    Return the actual free mem on host.
    """
    meminfo = utils.readMemInfo()
    return (meminfo['MemFree'] +
            meminfo['Cached'] +
            meminfo['Buffers'] +
            meminfo['SReclaimable']) * KiB


def _countVms(cif):
    count = active = incoming = outgoing = 0
    for vmId, v in cif.getVMs().items():
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

            # TODO: move to a periodic worker and cache the result
            stats = instance.get_all_stats(timeout=5)
            if 0 in stats:
                i['globalMaintenance'] = stats[0].get(
                    haClient.HAClient.GlobalMdFlags.MAINTENANCE,
                    False)
            if host_id in stats:
                i['active'] = stats[host_id]['live-data']
                i['score'] = stats[host_id]['score']
                i['localMaintenance'] = stats[host_id]['maintenance']
        except IOError as e:
            if e.errno == errno.ENOENT:
                logging.warning(
                    "Failed to retrieve Hosted Engine HA info, is Hosted "
                    "Engine setup finished?")
            else:
                logging.warning(
                    "Failed to retrieve Hosted Engine HA info: %s", e)
        except Exception:
            logging.exception("Failed to retrieve Hosted Engine HA info")
    return i
