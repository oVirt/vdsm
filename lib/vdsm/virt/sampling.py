#
# Copyright 2008-2018 Red Hat, Inc.
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
from __future__ import division

import libvirt
import six
from six.moves import map

"""
Support for VM and host statistics sampling.
"""

from collections import defaultdict, deque, namedtuple
import logging
import os
import re
import threading
import time

from vdsm import hugepages
from vdsm import numa
from vdsm import utils
import vdsm.common.time
from vdsm.common.units import KiB, MiB
from vdsm.config import config
from vdsm.constants import P_VDSM_RUN
from vdsm.host import api as hostapi
from vdsm.virt.utils import ExpiringCache


_THP_STATE_PATH = '/sys/kernel/mm/transparent_hugepage/enabled'
if not os.path.exists(_THP_STATE_PATH):
    _THP_STATE_PATH = '/sys/kernel/mm/redhat_transparent_hugepage/enabled'
_METRICS_ENABLED = config.getboolean('metrics', 'enabled')
_NOWAIT_ENABLED = config.getboolean('vars', 'nowait_domain_stats')


class TotalCpuSample(object):
    """
    A sample of total CPU consumption.

    The sample is taken at initialization time and can't be updated.
    """
    def __init__(self):
        with open('/proc/stat') as f:
            self.user, userNice, self.sys, self.idle = \
                map(int, f.readline().split()[1:5])
        self.user += userNice


class CpuCoreSample(object):
    """
    A sample of the CPU consumption of each core

    The sample is taken at initialization time and can't be updated.
    """
    CPU_CORE_STATS_PATTERN = re.compile(r'cpu(\d+)\s+(.*)')

    def __init__(self):
        self.coresSample = {}
        with open('/proc/stat') as src:
            for line in src:
                match = self.CPU_CORE_STATS_PATTERN.match(line)
                if match:
                    coreSample = {}
                    user, userNice, sys, idle = \
                        map(int, match.group(2).split()[0:4])
                    coreSample['user'] = user
                    coreSample['userNice'] = userNice
                    coreSample['sys'] = sys
                    coreSample['idle'] = idle
                    self.coresSample[match.group(1)] = coreSample

    def getCoreSample(self, coreId):
        strCoreId = str(coreId)
        return self.coresSample.get(strCoreId)


class NumaNodeMemorySample(object):
    """
    A sample of the memory stats of each numa node

    The sample is taken at initialization time and can't be updated.
    """
    def __init__(self):
        self.nodesMemSample = {}
        numaTopology = numa.topology()
        for nodeIndex in numaTopology:
            nodeMemSample = {}
            # work around libvirt bug (if not built with numactl)
            if len(numaTopology) == 1:
                idx = -1
            else:
                idx = int(nodeIndex)
            memInfo = numa.memory_by_cell(idx)
            nodeMemSample['memFree'] = memInfo['free']
            # in case the numa node has zero memory assigned, report the whole
            # memory as used
            nodeMemSample['memPercent'] = 100
            if int(memInfo['total']) != 0:
                nodeMemSample['memPercent'] = 100 - \
                    int(100.0 * int(memInfo['free']) // int(memInfo['total']))
            page_sizes = list(numaTopology[nodeIndex]['hugepages'].keys())
            nodeMemSample['hugepages'] = \
                numa.free_pages_by_cell(page_sizes, idx)
            self.nodesMemSample[nodeIndex] = nodeMemSample


class PidCpuSample(object):
    """
    A sample of the CPU consumption of a process.

    The sample is taken at initialization time and can't be updated.
    """
    def __init__(self, pid):
        with open('/proc/%s/stat' % pid) as stat:
            self.user, self.sys = \
                map(int, stat.read().split()[13:15])


class HostSample(object):
    """
    A sample of host-related statistics.

    Contains the state of the host at the time of initialization.
    """
    MONITORED_PATHS = ['/tmp', '/var/log', P_VDSM_RUN]

    def _getDiskStats(self):
        d = {}
        for p in self.MONITORED_PATHS:
            free = 0
            try:
                stat = os.statvfs(p)
                free = stat.f_bavail * stat.f_bsize // MiB
            except:
                pass
            d[p] = {'free': str(free)}
        return d

    def __init__(self, pid):
        """
        Initialize a HostSample.

        :param pid: The PID of this vdsm host.
        :type pid: int
        """
        self.timestamp = time.time()
        self.pidcpu = PidCpuSample(pid)
        self.ncpus = os.sysconf('SC_NPROCESSORS_ONLN')
        self.totcpu = TotalCpuSample()
        meminfo = utils.readMemInfo()
        freeOrCached = (meminfo['MemFree'] +
                        meminfo['Cached'] + meminfo['Buffers'])
        self.memUsed = 100 - int(100.0 * (freeOrCached) / meminfo['MemTotal'])
        self.anonHugePages = meminfo.get('AnonHugePages', 0) // KiB
        try:
            with open('/proc/loadavg') as loadavg:
                self.cpuLoad = loadavg.read().split()[1]
        except:
            self.cpuLoad = '0.0'
        self.diskStats = self._getDiskStats()
        try:
            with open(_THP_STATE_PATH) as f:
                s = f.read()
                self.thpState = s[s.index('[') + 1:s.index(']')]
        except:
            self.thpState = 'never'
        self.hugepages = hugepages.state()
        self.cpuCores = CpuCoreSample()
        self.numaNodeMem = NumaNodeMemorySample()


_MINIMUM_SAMPLES = 1


class SampleWindow(object):
    """Keep sliding window of samples."""

    def __init__(self, size, timefn=time.time):
        if size < _MINIMUM_SAMPLES:
            raise ValueError("window size must be not less than %i" %
                             _MINIMUM_SAMPLES)

        self._samples = deque(maxlen=size)
        self._timefn = timefn

    def append(self, value):
        """
        Record the current time and append new sample, removing the oldest
        sample if needed.
        """
        timestamp = self._timefn()
        self._samples.append((timestamp, value))

    def stats(self):
        """
        Return a tuple in the format: (first, last, difftime), containing
        the first and the last samples in the defined 'window' and the
        time difference between them.
        """
        if len(self._samples) < 2:
            return None, None, None

        first_timestamp, first_sample = self._samples[0]
        last_timestamp, last_sample = self._samples[-1]

        elapsed_time = last_timestamp - first_timestamp
        return first_sample, last_sample, elapsed_time

    def last(self, nth=1):
        """
        Return the nth-last collected sample, and its timestamp.
        Return (None, None) if the nth-last collected sample doesn't exist.
        """
        if len(self._samples) < nth:
            return None, None
        return self._samples[-nth]


_StatsSample = namedtuple('StatsSample',
                          ['first_value', 'last_value',
                           'interval', 'stats_age'])


class StatsSample(_StatsSample):
    def is_empty(self):
        return (
            self.first_value is None and
            self.last_value is None and
            self.interval is None
        )


class StatsCache(object):
    """
    Cache for bulk stats samples.
    Provide facilities to retrieve per-vm samples, and the glue code to deal
    with disappearing per-vm samples.

    Rationale for the 'clock()' method and for the odd API of the 'put()'
    method with explicit 'monotonic_ts' argument:

    QEMU processes can go rogue and block on a sampling operation, most
    likely, but not only, because storage becomes unavailable.
    In turn, that means that libvirt API that VDSM uses can get stuck,
    but eventually those calls can unblock.

    VDSM has countermeasures for those cases. Stuck threads are replaced,
    thanks to Executor. But still, before to be destroyed, a replaced
    thread can mistakenly try to add a sample to a StatsCache.

    Because of worker thread replacement, that sample from stuck thread
    can  be stale.
    So, under the assumption that at stable state stats collection has
    a time cost negligible with respect the collection interval, we need
    to take the sample timestamp BEFORE to start the possibly-blocking call.
    If we take the timestamp after the call, we have no means to distinguish
    between a well behaving call and an unblocked stuck call.
    """

    _log = logging.getLogger("virt.sampling.StatsCache")

    def __init__(self, clock=vdsm.common.time.monotonic_time):
        self._clock = clock
        self._lock = threading.Lock()
        self._samples = SampleWindow(size=2, timefn=self._clock)
        self._last_sample_time = 0
        self._vm_last_timestamp = defaultdict(int)

    def add(self, vmid):
        """
        Warm up the cache for the given VM.
        This is to avoid races during the first sampling and the first
        reporting, which may result in a VM wrongly reported as unresponsive.
        """
        with self._lock:
            self._vm_last_timestamp[vmid] = self._clock()

    def remove(self, vmid):
        """
        Remove any data from the cache related to the given VM.
        """
        with self._lock:
            del self._vm_last_timestamp[vmid]

    def get(self, vmid):
        """
        Return the available StatSample for the given VM.
        """
        with self._lock:
            first_batch, last_batch, interval = self._samples.stats()
            stats_age = self._clock() - self._vm_last_timestamp[vmid]

            if first_batch is None:
                return StatsSample(None, None, None, stats_age)

            first_sample = first_batch.get(vmid)
            last_sample = last_batch.get(vmid)

            if first_sample is None or last_sample is None:
                return StatsSample(None, None, None, stats_age)

            return StatsSample(first_sample, last_sample,
                               interval, stats_age)

    def get_batch(self):
        """
        Return the available StatSample for the all VMs.
        """
        with self._lock:
            first_batch, last_batch, interval = self._samples.stats()

            if first_batch is None:
                return None

            ts = self._clock()
            return {
                vm_id: StatsSample(
                    first_batch[vm_id], last_batch[vm_id], interval,
                    ts - self._vm_last_timestamp[vm_id]
                )
                for vm_id in last_batch if (vm_id in first_batch and
                                            vm_id in self._vm_last_timestamp)
            }

    def clock(self):
        """
        Provide timestamp compatible with what put() expects
        """
        return self._clock()

    def put(self, bulk_stats, monotonic_ts):
        """
        Add a new bulk sample to the collection.
        `monotonic_ts' is the sample time which must be associated with
        the sample.
        Discard silently out of order samples, which are assumed to be
        returned by unblocked stuck calls, to avoid overwrite fresh data
        with stale one.
        """
        with self._lock:
            last_sample_time = self._last_sample_time
            if monotonic_ts >= last_sample_time:
                self._samples.append(bulk_stats)
                self._last_sample_time = monotonic_ts

                self._update_ts(bulk_stats, monotonic_ts)
            else:
                self._log.warning(
                    'dropped stale old sample: sampled %f stored %f',
                    monotonic_ts, last_sample_time)

    def _update_ts(self, bulk_stats, monotonic_ts):
        # FIXME: this is expected to be costly performance-wise.
        for vmid in bulk_stats:
            self._vm_last_timestamp[vmid] = monotonic_ts


stats_cache = StatsCache()


# this value can be tricky to tune.
# we should avoid as much as we can to trigger
# false positive fast flows (getAllDomainStats call).
# to do so, we should avoid this value to be
# a multiple of known timeout and period:
# - vm sampling period is 15s (we control that)
# - libvirt (default) qemu monitor timeout is 30s (we DON'T contol this)
_TTL = 40.0


BULK_STATS_TYPES = (
    libvirt.VIR_DOMAIN_STATS_STATE |
    libvirt.VIR_DOMAIN_STATS_CPU_TOTAL |
    libvirt.VIR_DOMAIN_STATS_BALLOON |
    libvirt.VIR_DOMAIN_STATS_VCPU |
    libvirt.VIR_DOMAIN_STATS_INTERFACE |
    libvirt.VIR_DOMAIN_STATS_BLOCK
)


class VMBulkstatsMonitor(object):
    def __init__(self, conn, get_vms, stats_cache,
                 stats_types=BULK_STATS_TYPES, ttl=_TTL):
        self._conn = conn
        self._get_vms = get_vms
        self._stats_cache = stats_cache
        self._stats_types = stats_types
        self._skip_doms = ExpiringCache(ttl)
        self._sampling = threading.Semaphore()  # used as glorified counter
        self._log = logging.getLogger("virt.sampling.VMBulkstatsMonitor")

    def __call__(self):
        log_status = True
        timestamp = self._stats_cache.clock()
        acquired = self._sampling.acquire(blocking=False)
        # we are deep in the hot path. bool(ExpiringCache)
        # *is* costly so we should avoid it if we can.
        fast_path = acquired and not self._skip_doms
        responsive_doms = []
        flags = libvirt.VIR_CONNECT_GET_ALL_DOMAINS_STATS_RUNNING
        if _NOWAIT_ENABLED:
            flags |= libvirt.VIR_CONNECT_GET_ALL_DOMAINS_STATS_NOWAIT
        try:
            if fast_path:
                # This is expected to be the common case.
                # If everything's ok, we can skip all the costly checks.
                bulk_stats = self._conn.getAllDomainStats(
                    stats=self._stats_types, flags=flags)
            else:
                # A previous call got stuck, or not every domain
                # has properly recovered. Thus we query only responsive ones.
                responsive_doms = self._get_responsive_doms()
                if responsive_doms:
                    bulk_stats = self._conn.domainListGetStats(
                        responsive_doms, stats=self._stats_types, flags=flags)
                else:
                    bulk_stats = []
        except Exception:
            self._log.exception("vm sampling failed")
            log_status = False
        else:
            self._stats_cache.put(_translate(bulk_stats), timestamp)
        finally:
            if acquired:
                self._sampling.release()
        if log_status:
            self._log.debug(
                'sampled timestamp %r elapsed %.3f acquired %r domains %s',
                timestamp, self._stats_cache.clock() - timestamp, acquired,
                'all' if fast_path else len(responsive_doms))

    def _get_responsive_doms(self):
        vms = self._get_vms()
        doms = []
        for vm_id, vm_obj in six.iteritems(vms):
            to_skip = self._skip_doms.get(vm_id, False)
            if to_skip:
                continue
            elif not vm_obj.isDomainReadyForCommands():
                self._skip_doms[vm_id] = True
            else:
                # TODO: This racy check may fail if the underlying libvirt
                # domain has died just after checking isDomainReadyForCommands
                # succeeded.
                doms.append(vm_obj._dom._dom)
        return doms


HOST_STATS_AVERAGING_WINDOW = 2


host_samples = SampleWindow(size=HOST_STATS_AVERAGING_WINDOW)


class HostMonitor(object):

    def __init__(self, samples=host_samples, cif=None):
        self._samples = samples
        self._pid = os.getpid()
        self._cif = cif

    def __call__(self):
        sample = HostSample(self._pid)
        self._samples.append(sample)

        if self._cif and _METRICS_ENABLED:
            stats = hostapi.get_stats(self._cif, self._samples.stats())
            hostapi.send_metrics(stats)


def _translate(bulk_stats):
    return dict((dom.UUIDString(), stats)
                for dom, stats in bulk_stats)
