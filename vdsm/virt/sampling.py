#
# Copyright 2008-2014 Red Hat, Inc.
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

"""
Support for VM and host statistics sampling.
"""

from collections import deque
import threading
import os
import time
import logging
import errno
import re

from vdsm import utils
from vdsm import netinfo
from vdsm import ipwrapper
from vdsm.constants import P_VDSM_RUN, P_VDSM_CLIENT_LOG

import caps

_THP_STATE_PATH = '/sys/kernel/mm/transparent_hugepage/enabled'
if not os.path.exists(_THP_STATE_PATH):
    _THP_STATE_PATH = '/sys/kernel/mm/redhat_transparent_hugepage/enabled'


class InterfaceSample(object):
    """
    A network interface sample.

    The sample is set at the time of initialization and can't be updated.
    """
    def readIfaceStat(self, ifid, stat):
        """
        Get and interface's stat.

        .. note::
            Really ugly implementation; from time to time, Linux returns an
            empty line. TODO: understand why this happens and fix it!

        :param ifid: The ID of the interface you want to query.
        :param stat: The type of statistic you want get.

        :returns: The value of the statistic you asked for.
        :type: int
        """
        f = '/sys/class/net/%s/statistics/%s' % (ifid, stat)
        tries = 5
        while tries:
            tries -= 1
            try:
                with open(f) as fi:
                    s = fi.read()
            except IOError as e:
                # silently ignore missing wifi stats
                if e.errno != errno.ENOENT:
                    logging.debug("Could not read %s", f, exc_info=True)
                return 0
            try:
                return int(s)
            except:
                if s != '':
                    logging.warning("Could not parse statistics (%s) from %s",
                                    f, s, exc_info=True)
                logging.debug('bad %s: (%s)', f, s)
                if not tries:
                    raise

    def __init__(self, link):
        ifid = link.name
        self.rx = self.readIfaceStat(ifid, 'rx_bytes')
        self.tx = self.readIfaceStat(ifid, 'tx_bytes')
        self.rxDropped = self.readIfaceStat(ifid, 'rx_dropped')
        self.txDropped = self.readIfaceStat(ifid, 'tx_dropped')
        self.rxErrors = self.readIfaceStat(ifid, 'rx_errors')
        self.txErrors = self.readIfaceStat(ifid, 'tx_errors')
        self.operstate = 'up' if link.oper_up else 'down'
        self.speed = _getLinkSpeed(link)
        self.duplex = _getDuplex(ifid)

    _LOGGED_ATTRS = ('operstate', 'speed', 'duplex')

    def _log_attrs(self, attrs):
        return ' '.join(
            '%s:%s' % (attr, getattr(self, attr)) for attr in attrs)

    def to_connlog(self):
        return self._log_attrs(self._LOGGED_ATTRS)

    def connlog_diff(self, other):
        """Return a textual description of the interesting stuff new to self
        and missing in 'other'."""

        return self._log_attrs(
            attr for attr in self._LOGGED_ATTRS
            if getattr(self, attr) != getattr(other, attr))


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
        if strCoreId in self.coresSample:
            return self.coresSample[strCoreId]


class NumaNodeMemorySample(object):
    """
    A sample of the memory stats of each numa node

    The sample is taken at initialization time and can't be updated.
    """
    def __init__(self):
        self.nodesMemSample = {}
        numaTopology = caps.getNumaTopology()
        for nodeIndex in numaTopology:
            nodeMemSample = {}
            if len(numaTopology) < 2:
                memInfo = caps.getUMAHostMemoryStats()
            else:
                memInfo = caps.getMemoryStatsByNumaCell(int(nodeIndex))
            nodeMemSample['memFree'] = memInfo['free']
            nodeMemSample['memPercent'] = 100 - \
                int(100.0 * int(memInfo['free']) / int(memInfo['total']))
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


class TimedSample(object):
    def __init__(self):
        self.timestamp = time.time()


_PROC_STAT_PATH = '/proc/stat'


def getBootTime():
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


def _get_interfaces_and_samples():
    links_and_samples = {}
    for link in ipwrapper.getLinks():
        try:
            links_and_samples[link.name] = InterfaceSample(link)
        except IOError as e:
            # this handles a race condition where the device is now no
            # longer exists and netlink fails to fetch it
            if e.errno == errno.ENODEV:
                continue
            raise
    return links_and_samples


class HostSample(TimedSample):
    """
    A sample of host-related statistics.

    Contains the sate of the host in the time of initialization.
    """
    MONITORED_PATHS = ['/tmp', '/var/log', '/var/log/core', P_VDSM_RUN]

    def _getDiskStats(self):
        d = {}
        for p in self.MONITORED_PATHS:
            free = 0
            try:
                stat = os.statvfs(p)
                free = stat.f_bavail * stat.f_bsize / (2 ** 20)
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
        super(HostSample, self).__init__()
        self.interfaces = _get_interfaces_and_samples()
        self.pidcpu = PidCpuSample(pid)
        self.totcpu = TotalCpuSample()
        meminfo = utils.readMemInfo()
        freeOrCached = (meminfo['MemFree'] +
                        meminfo['Cached'] + meminfo['Buffers'])
        self.memUsed = 100 - int(100.0 * (freeOrCached) / meminfo['MemTotal'])
        self.anonHugePages = meminfo.get('AnonHugePages', 0) / 1024
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
        self.cpuCores = CpuCoreSample()
        self.numaNodeMem = NumaNodeMemorySample()
        ENGINE_DEFAULT_POLL_INTERVAL = 15
        try:
            self.recentClient = (
                self.timestamp - os.stat(P_VDSM_CLIENT_LOG).st_mtime <
                2 * ENGINE_DEFAULT_POLL_INTERVAL)
        except OSError as e:
            if e.errno == errno.ENOENT:
                self.recentClient = False
            else:
                raise

    def to_connlog(self):
        text = ', '.join(
            ('%s:(%s)' % (ifid, ifacesample.to_connlog()))
            for (ifid, ifacesample) in self.interfaces.iteritems())
        return ('recent_client:%s, ' % self.recentClient) + text

    def connlog_diff(self, other):
        text = ''
        for ifid, sample in self.interfaces.iteritems():
            if ifid in other.interfaces:
                diff = sample.connlog_diff(other.interfaces[ifid])
                if diff:
                    text += '%s:(%s) ' % (ifid, diff)
            else:
                text += 'new %s:(%s) ' % (ifid, sample.to_connlog())

        for ifid, sample in other.interfaces.iteritems():
            if ifid not in self.interfaces:
                text += 'dropped %s:(%s) ' % (ifid, sample.to_connlog())

        if self.recentClient != other.recentClient:
            text += 'recent_client:%s' % self.recentClient

        return text


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

    def last(self):
        """
        Return the last collected sample.
        """
        if not self._samples:
            return None
        _, last_sample = self._samples[-1]
        return last_sample


class AdvancedStatsFunction(object):
    """
    A wrapper for functions and methods that will be executed at regular
    intervals storing the return values for statistic purpose.
    It is possible to provide a custom time function 'timefn' that provides
    cached values to reduce system calls.
    """
    def __init__(self, function, interval=1, window=_MINIMUM_SAMPLES,
                 timefn=time.time):
        self._function = function

        if not isinstance(interval, int) or interval < 1:
            raise ValueError("interval must be int and greater than 0")

        self._interval = interval
        self._samples = SampleWindow(window, timefn)

    @property
    def interval(self):
        return self._interval

    def __repr__(self):
        return "<AdvancedStatsFunction %s at 0x%x>" % (
            self._function.__name__, id(self._function.__name__))

    def __call__(self, *args, **kwargs):
        value = self._function(*args, **kwargs)
        self._samples.append(value)
        return value

    def getStats(self):
        return self._samples.stats()

    def getLastSample(self):
        return self._samples.last()


class AdvancedStatsThread(threading.Thread):
    """
    A thread that runs the registered AdvancedStatsFunction objects
    for statistic and monitoring purpose.
    """
    DEFAULT_LOG = logging.getLogger("AdvancedStatsThread")

    def __init__(self, log=DEFAULT_LOG, daemon=False):
        """
        Initialize an AdvancedStatsThread object
        """
        threading.Thread.__init__(self)
        self.daemon = daemon

        self._log = log
        self._stopEvent = threading.Event()
        self._contEvent = threading.Event()

        self._statsTime = None
        self._statsFunctions = []

    def addStatsFunction(self, *args):
        """
        Register the functions listed as arguments
        """
        if self.isAlive():
            raise RuntimeError("AdvancedStatsThread is started")

        for statsFunction in args:
            self._statsFunctions.append(statsFunction)

    def start(self):
        """
        Start the execution of the thread and exit
        """
        self._log.debug("Start statistics collection")
        threading.Thread.start(self)

    def stop(self):
        """
        Stop the execution of the thread and exit
        """
        self._log.debug("Stop statistics collection")
        self._stopEvent.set()
        self._contEvent.set()

    def pause(self):
        """
        Pause the execution of the registered functions
        """
        self._log.debug("Pause statistics collection")
        self._contEvent.clear()

    def cont(self):
        """
        Resume the execution of the registered functions
        """
        self._log.debug("Resume statistics collection")
        self._contEvent.set()

    def getLastSampleTime(self):
        return self._statsTime

    def run(self):
        self._log.debug("Stats thread started")
        self._contEvent.set()

        while not self._stopEvent.isSet():
            try:
                self.collect()
            except:
                self._log.debug("Stats thread failed", exc_info=True)

        self._log.debug("Stats thread finished")

    def handleStatsException(self, ex):
        """
        Handle the registered function exceptions and eventually stop the
        sampling if a fatal error occurred.
        """
        return False

    def collect(self):
        # TODO: improve this with lcm
        _mInt = map(lambda x: x.interval, self._statsFunctions)
        maxInterval = reduce(lambda x, y: x * y, set(_mInt), 1)

        intervalAccum = 0
        while not self._stopEvent.isSet():
            self._contEvent.wait()

            self._statsTime = time.time()
            waitInterval = maxInterval

            for statsFunction in self._statsFunctions:
                thisInterval = statsFunction.interval - (
                    intervalAccum % statsFunction.interval)
                waitInterval = min(waitInterval, thisInterval)

                if intervalAccum % statsFunction.interval == 0:
                    try:
                        statsFunction()
                    except Exception as e:
                        if not self.handleStatsException(e):
                            self._log.exception("Stats function failed: %s",
                                                statsFunction)

            self._stopEvent.wait(waitInterval)
            intervalAccum = (intervalAccum + waitInterval) % maxInterval


class HostStatsThread(threading.Thread):
    """
    A thread that periodically samples host statistics.
    """
    AVERAGING_WINDOW = 5
    SAMPLE_INTERVAL_SEC = 2
    _CONNLOG = logging.getLogger('connectivity')

    def __init__(self, log):
        self.startTime = time.time()

        threading.Thread.__init__(self)
        self.daemon = True
        self._log = log
        self._stopEvent = threading.Event()
        self._samples = []
        self._lastSampleTime = time.time()

        self._pid = os.getpid()
        self._ncpus = max(os.sysconf('SC_NPROCESSORS_ONLN'), 1)

    def stop(self):
        self._stopEvent.set()

    def sample(self):
        hs = HostSample(self._pid)
        return hs

    def run(self):
        import vm
        try:
            # wait a bit before starting to sample
            time.sleep(self.SAMPLE_INTERVAL_SEC)
            while not self._stopEvent.isSet():
                try:
                    sample = self.sample()
                    self._samples.append(sample)
                    if len(self._samples) == 1:
                        self._CONNLOG.debug('%s', sample.to_connlog())
                    else:
                        diff = sample.connlog_diff(self._samples[-2])
                        if diff:
                            self._CONNLOG.debug('%s', diff)

                    self._lastSampleTime = sample.timestamp
                    if len(self._samples) > self.AVERAGING_WINDOW:
                        self._samples.pop(0)
                except vm.TimeoutError:
                    self._log.exception("Timeout while sampling stats")
                self._stopEvent.wait(self.SAMPLE_INTERVAL_SEC)
        except:
            if not self._stopEvent.isSet():
                self._log.exception("Error while sampling stats")

    @utils.memoized
    def _boot_time(self):
        # Try to get boot time only once, if N/A just log the error and never
        # include it in the response.
        try:
            return getBootTime()
        except (IOError, ValueError):
            self._log.exception('Failed to get boot time')
            return None

    def get(self):
        stats = self._getInterfacesStats()
        stats['cpuSysVdsmd'] = stats['cpuUserVdsmd'] = 0.0
        stats['elapsedTime'] = int(time.time() - self.startTime)
        if len(self._samples) < 2:
            return stats
        hs0, hs1 = self._samples[0], self._samples[-1]
        interval = hs1.timestamp - hs0.timestamp
        jiffies = (hs1.pidcpu.user - hs0.pidcpu.user) % (2 ** 32)
        stats['cpuUserVdsmd'] = jiffies / interval
        jiffies = (hs1.pidcpu.sys - hs0.pidcpu.sys) % (2 ** 32)
        stats['cpuSysVdsmd'] = jiffies / interval

        jiffies = (hs1.totcpu.user - hs0.totcpu.user) % (2 ** 32)
        stats['cpuUser'] = jiffies / interval / self._ncpus
        jiffies = (hs1.totcpu.sys - hs0.totcpu.sys) % (2 ** 32)
        stats['cpuSys'] = jiffies / interval / self._ncpus
        stats['cpuIdle'] = max(0.0,
                               100.0 - stats['cpuUser'] - stats['cpuSys'])
        stats['memUsed'] = hs1.memUsed
        stats['anonHugePages'] = hs1.anonHugePages
        stats['cpuLoad'] = hs1.cpuLoad

        stats['diskStats'] = hs1.diskStats
        stats['thpState'] = hs1.thpState

        if self._boot_time():
            stats['bootTime'] = self._boot_time()

        stats['numaNodeMemFree'] = hs1.numaNodeMem.nodesMemSample
        stats['cpuStatistics'] = self._getCpuCoresStats()
        return stats

    def _getCpuCoresStats(self):
        """
        :returns: a dict that with the following formats:

            {'<cpuId>': {'numaNodeIndex': int, 'cpuSys': 'str',
             'cpuIdle': 'str', 'cpuUser': 'str'}, ...}
        """
        cpuCoreStats = {}
        for nodeIndex, numaNode in caps.getNumaTopology().iteritems():
            cpuCores = numaNode['cpus']
            for cpuCore in cpuCores:
                coreStat = {}
                coreStat['nodeIndex'] = int(nodeIndex)
                hs0, hs1 = self._samples[0], self._samples[-1]
                interval = hs1.timestamp - hs0.timestamp
                jiffies = (hs1.cpuCores.getCoreSample(cpuCore)['user'] -
                           hs0.cpuCores.getCoreSample(cpuCore)['user']) % \
                    (2 ** 32)
                coreStat['cpuUser'] = ("%.2f" % (jiffies / interval))
                jiffies = (hs1.cpuCores.getCoreSample(cpuCore)['sys'] -
                           hs0.cpuCores.getCoreSample(cpuCore)['sys']) % \
                    (2 ** 32)
                coreStat['cpuSys'] = ("%.2f" % (jiffies / interval))
                coreStat['cpuIdle'] = ("%.2f" %
                                       max(0.0, 100.0 -
                                           float(coreStat['cpuUser']) -
                                           float(coreStat['cpuSys'])))
                cpuCoreStats[str(cpuCore)] = coreStat
        return cpuCoreStats

    def _getInterfacesStats(self):
        """
        Compile and return a dict containing the stats.

        :returns: a dict that with the following keys:

            * cpuUser
            * cpuSys
            * cpuIdle
            * rxRate
            * txRate
        """
        stats = {'cpuUser': 0.0, 'cpuSys': 0.0, 'cpuIdle': 100.0,
                 'rxRate': 0.0, 'txRate': 0.0,
                 'statsAge': time.time() - self._lastSampleTime}
        if len(self._samples) < 2:
            return stats
        hs0, hs1 = self._samples[0], self._samples[-1]
        interval = hs1.timestamp - hs0.timestamp

        rx = tx = rxDropped = txDropped = 0
        stats['network'] = {}
        total_rate = 0
        for ifid in hs1.interfaces:
            # it skips hot-plugged devices if we haven't enough information
            # to count stats from it
            if ifid not in hs0.interfaces:
                continue
            ifrate = hs1.interfaces[ifid].speed or 1000
            Mbps2Bps = (10 ** 6) / 8
            thisRx = (hs1.interfaces[ifid].rx - hs0.interfaces[ifid].rx) % \
                (2 ** 32)
            thisTx = (hs1.interfaces[ifid].tx - hs0.interfaces[ifid].tx) % \
                (2 ** 32)
            rxRate = 100.0 * thisRx / interval / ifrate / Mbps2Bps
            txRate = 100.0 * thisTx / interval / ifrate / Mbps2Bps
            if txRate > 100 or rxRate > 100:
                txRate = min(txRate, 100.0)
                rxRate = min(rxRate, 100.0)
                self._log.debug('Rate above 100%%. DEBUG: ifid %s interval: '
                                '%s thisRx %s thisTx %s samples %s', ifid,
                                interval, thisRx, thisTx,
                                [(hs.timestamp, hs.interfaces[ifid].rx,
                                 hs.interfaces[ifid].tx)
                                 for hs in self._samples if
                                 ifid in hs.interfaces])
            iface = hs1.interfaces[ifid]
            stats['network'][ifid] = {'name': ifid, 'speed': str(ifrate),
                                      'rxDropped': str(iface.rxDropped),
                                      'txDropped': str(iface.txDropped),
                                      'rxErrors': str(iface.rxErrors),
                                      'txErrors': str(iface.txErrors),
                                      'state': iface.operstate,
                                      'rxRate': '%.1f' % rxRate,
                                      'txRate': '%.1f' % txRate,
                                      }
            rx += thisRx
            tx += thisTx
            rxDropped += hs1.interfaces[ifid].rxDropped
            txDropped += hs1.interfaces[ifid].txDropped
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


def _getLinkSpeed(dev):
    if dev.isNIC():
        speed = netinfo.nicSpeed(dev.name)
    elif dev.isBOND():
        speed = netinfo.bondSpeed(dev.name)
    elif dev.isVLAN():
        speed = netinfo.vlanSpeed(dev.name)
    else:
        speed = 0
    return speed


def _getDuplex(ifid):
    """Return whether a device is connected in full-duplex. Return 'unknown' if
    duplex state is not known"""
    try:
        with open('/sys/class/net/%s/duplex' % ifid) as src:
            return src.read().strip()
    except IOError:
        return 'unknown'
