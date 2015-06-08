#
# Copyright 2008-2015 Red Hat, Inc.
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

import logging

import six

from vdsm import utils

import caps
import v2v


JIFFIES_BOUND = 2 ** 32
NETSTATS_BOUND = 2 ** 32


_clock = utils.monotonic_time
_start_time = 0


def _elapsed_time():
    return _clock() - _start_time


def start(clock=utils.monotonic_time):
    global _clock
    global _start_time
    _clock = clock
    _start_time = _clock()


def produce(first_sample, last_sample):
    stats = _empty_stats()

    if first_sample is None:
        return stats

    stats.update(_get_interfaces_stats(first_sample, last_sample))

    interval = last_sample.timestamp - first_sample.timestamp

    jiffies = (
        last_sample.pidcpu.user - first_sample.pidcpu.user
    ) % JIFFIES_BOUND
    stats['cpuUserVdsmd'] = jiffies / interval
    jiffies = (
        last_sample.pidcpu.sys - first_sample.pidcpu.sys
    ) % JIFFIES_BOUND
    stats['cpuSysVdsmd'] = jiffies / interval

    jiffies = (
        last_sample.totcpu.user - first_sample.totcpu.user
    ) % JIFFIES_BOUND
    stats['cpuUser'] = jiffies / interval / last_sample.ncpus
    jiffies = (
        last_sample.totcpu.sys - first_sample.totcpu.sys
    ) % JIFFIES_BOUND
    stats['cpuSys'] = jiffies / interval / last_sample.ncpus
    stats['cpuIdle'] = max(0.0,
                           100.0 - stats['cpuUser'] - stats['cpuSys'])
    stats['memUsed'] = last_sample.memUsed
    stats['anonHugePages'] = last_sample.anonHugePages
    stats['cpuLoad'] = last_sample.cpuLoad

    stats['diskStats'] = last_sample.diskStats
    stats['thpState'] = last_sample.thpState

    if _boot_time():
        stats['bootTime'] = _boot_time()

    stats['numaNodeMemFree'] = last_sample.numaNodeMem.nodesMemSample
    stats['cpuStatistics'] = _get_cpu_core_stats(
        first_sample, last_sample)

    stats['v2vJobs'] = v2v.get_jobs_status()
    return stats


def _get_cpu_core_stats(first_sample, last_sample):
    interval = last_sample.timestamp - first_sample.timestamp

    def compute_cpu_usage(cpu_core, mode):
        jiffies = (
            last_sample.cpuCores.getCoreSample(cpu_core)[mode] -
            first_sample.cpuCores.getCoreSample(cpu_core)[mode]
        ) % JIFFIES_BOUND
        return ("%.2f" % (jiffies / interval))

    cpu_core_stats = {}
    for node_index, numa_node in six.iteritems(caps.getNumaTopology()):
        cpu_cores = numa_node['cpus']
        for cpu_core in cpu_cores:
            core_stat = {
                'nodeIndex': int(node_index),
                'cpuUser': compute_cpu_usage(cpu_core, 'user'),
                'cpuSys': compute_cpu_usage(cpu_core, 'sys'),
            }
            core_stat['cpuIdle'] = (
                "%.2f" % max(0.0,
                             100.0 -
                             float(core_stat['cpuUser']) -
                             float(core_stat['cpuSys'])))
            cpu_core_stats[str(cpu_core)] = core_stat
    return cpu_core_stats


def _get_interfaces_stats(first_sample, last_sample):
    interval = last_sample.timestamp - first_sample.timestamp

    rx = tx = rxDropped = txDropped = 0
    stats = {'network': {}}
    total_rate = 0
    for ifid in last_sample.interfaces:
        # it skips hot-plugged devices if we haven't enough information
        # to count stats from it
        if ifid not in first_sample.interfaces:
            continue

        ifrate = last_sample.interfaces[ifid].speed or 1000
        Mbps2Bps = (10 ** 6) / 8
        thisRx = (
            last_sample.interfaces[ifid].rx -
            first_sample.interfaces[ifid].rx
            ) % NETSTATS_BOUND
        thisTx = (
            last_sample.interfaces[ifid].tx -
            first_sample.interfaces[ifid].tx
            ) % NETSTATS_BOUND
        rxRate = 100.0 * thisRx / interval / ifrate / Mbps2Bps
        txRate = 100.0 * thisTx / interval / ifrate / Mbps2Bps
        if txRate > 100 or rxRate > 100:
            txRate = min(txRate, 100.0)
            rxRate = min(rxRate, 100.0)
            logging.debug('Rate above 100%%.')
        iface = last_sample.interfaces[ifid]
        stats['network'][ifid] = {
            'name': ifid, 'speed': str(ifrate),
            'rxDropped': str(iface.rxDropped),
            'txDropped': str(iface.txDropped),
            'rxErrors': str(iface.rxErrors),
            'txErrors': str(iface.txErrors),
            'state': iface.operstate,
            'rxRate': '%.1f' % rxRate,
            'txRate': '%.1f' % txRate,
            'rx': str(iface.rx),
            'tx': str(iface.tx),
            'sampleTime': last_sample.timestamp,
        }
        rx += thisRx
        tx += thisTx
        rxDropped += last_sample.interfaces[ifid].rxDropped
        txDropped += last_sample.interfaces[ifid].txDropped
        total_rate += ifrate

    total_bytes_per_sec = (total_rate or 1000) * (10 ** 6) / 8
    stats['rxRate'] = 100.0 * rx / interval / total_bytes_per_sec
    stats['txRate'] = 100.0 * tx / interval / total_bytes_per_sec
    if stats['txRate'] > 100 or stats['rxRate'] > 100:
        stats['txRate'] = min(stats['txRate'], 100.0)
        stats['rxRate'] = min(stats['rxRate'], 100.0)
        logging.debug(stats)
    stats['rxDropped'] = rxDropped
    stats['txDropped'] = txDropped

    return stats


_PROC_STAT_PATH = '/proc/stat'


def get_boot_time():
    """
    Returns the boot time of the machine in seconds since epoch.

    Raises IOError if file access fails, or ValueError if boot time not
    present in file.
    """
    with open(_PROC_STAT_PATH) as proc_stat:
        for line in proc_stat:
            if line.startswith('btime'):
                parts = line.split()
                if len(parts) > 1:
                    return int(parts[1])
                else:
                    break
    raise ValueError('Boot time not present')


@utils.memoized
def _boot_time():
    # Try to get boot time only once, if N/A just log the error and never
    # include it in the response.
    try:
        return get_boot_time()
    except (IOError, ValueError):
        logging.exception('Failed to get boot time')
        return None


def _empty_stats():
    return {
        'cpuUser': 0.0,
        'cpuSys': 0.0,
        'cpuIdle': 100.0,
        'rxRate': 0.0,  # REQUIRED_FOR: engine < 3.6
        'txRate': 0.0,  # REQUIRED_FOR: engine < 3.6
        'cpuSysVdsmd': 0.0,
        'cpuUserVdsmd': 0.0,
        'elapsedTime': _elapsed_time(),
    }
