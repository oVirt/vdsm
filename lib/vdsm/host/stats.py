# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import logging

import six

from vdsm import numa
from vdsm.common import cache
from vdsm.common import time
from vdsm.config import config

from vdsm import v2v
from vdsm.network import api as net_api


JIFFIES_BOUND = 2 ** 32
NETSTATS_BOUND = 2 ** 32
SMALLEST_INTERVAL = 1e-5


_clock = time.monotonic_time
_start_time = 0


def _elapsed_time():
    return _clock() - _start_time


def start(clock=time.monotonic_time):
    global _clock
    global _start_time
    _clock = clock
    _start_time = _clock()


def produce(first_sample, last_sample):
    stats = _empty_stats()

    if first_sample is None:
        return stats

    interval = last_sample.timestamp - first_sample.timestamp

    # Prevents division by 0 and negative interval
    if interval < SMALLEST_INTERVAL:
        logging.warning('Sampling interval %f is too small (expected %f).',
                        interval,
                        config.getint('vars', 'host_sample_stats_interval'))
        return stats

    stats.update(get_interfaces_stats())

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
    stats['hugepages'] = last_sample.hugepages
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
        first_core_sample = first_sample.cpuCores.getCoreSample(cpu_core)
        last_core_sample = last_sample.cpuCores.getCoreSample(cpu_core)
        if not first_core_sample or not last_core_sample:
            raise MissingSample()
        jiffies = (
            last_core_sample[mode] - first_core_sample[mode]
        ) % JIFFIES_BOUND
        return ("%.2f" % (jiffies / interval))

    cpu_core_stats = {}
    for node_index, numa_node in six.iteritems(numa.topology()):
        cpu_cores = numa_node['cpus']
        for cpu_core in cpu_cores:
            try:
                user_cpu_usage = compute_cpu_usage(cpu_core, 'user')
                system_cpu_usage = compute_cpu_usage(cpu_core, 'sys')
            except MissingSample:
                # Only collect data when all required samples already present
                continue
            core_stat = {
                'nodeIndex': int(node_index),
                'cpuUser': user_cpu_usage,
                'cpuSys': system_cpu_usage,
            }
            core_stat['cpuIdle'] = (
                "%.2f" % max(0.0,
                             100.0 -
                             float(core_stat['cpuUser']) -
                             float(core_stat['cpuSys'])))
            cpu_core_stats[str(cpu_core)] = core_stat
    return cpu_core_stats


class MissingSample(Exception):
    pass


def get_interfaces_stats():
    return net_api.network_stats()


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


@cache.memoized
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
        'cpuLoad': 0.0,
        'cpuUser': 0.0,
        'cpuSys': 0.0,
        'cpuIdle': 100.0,
        'cpuSysVdsmd': 0.0,
        'cpuUserVdsmd': 0.0,
        'elapsedTime': _elapsed_time(),
        'memUsed': 0.0,
        'anonHugePages': 0.0,
    }
