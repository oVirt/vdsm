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
A module containing miscellaneous functions and classes that are user
plentifuly around vdsm.

.. attribute:: utils.symbolerror

    Contains a reverse dictionary pointing from error string to its error code.
"""
import threading
import os
import time
import logging
import errno
import re

from vdsm import utils
from vdsm import netinfo
from vdsm.ipwrapper import getLinks
from vdsm.constants import P_VDSM_RUN, P_VDSM_CLIENT_LOG

import caps

_THP_STATE_PATH = '/sys/kernel/mm/transparent_hugepage/enabled'
if not os.path.exists(_THP_STATE_PATH):
    _THP_STATE_PATH = '/sys/kernel/mm/redhat_transparent_hugepage/enabled'


class InterfaceSample:
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
                s = file(f).read()
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


class TotalCpuSample:
    """
    A sample of total CPU consumption.

    The sample is taken at initialization time and can't be updated.
    """
    def __init__(self):
        self.user, userNice, self.sys, self.idle = \
            map(int, file('/proc/stat').readline().split()[1:5])
        self.user += userNice


class CpuCoreSample:
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


class NumaNodeMemorySample:
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


class PidCpuSample:
    """
    A sample of the CPU consumption of a process.

    The sample is taken at initialization time and can't be updated.
    """
    def __init__(self, pid):
        self.user, self.sys = \
            map(int, file('/proc/%s/stat' % pid).read().split()[13:15])


class TimedSample:
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
        TimedSample.__init__(self)
        self.interfaces = dict(
            (link.name, InterfaceSample(link)) for link in getLinks())
        self.pidcpu = PidCpuSample(pid)
        self.totcpu = TotalCpuSample()
        meminfo = utils.readMemInfo()
        freeOrCached = (meminfo['MemFree'] +
                        meminfo['Cached'] + meminfo['Buffers'])
        self.memUsed = 100 - int(100.0 * (freeOrCached) / meminfo['MemTotal'])
        self.anonHugePages = meminfo.get('AnonHugePages', 0) / 1024
        try:
            self.cpuLoad = file('/proc/loadavg').read().split()[1]
        except:
            self.cpuLoad = '0.0'
        self.diskStats = self._getDiskStats()
        try:
            with file(_THP_STATE_PATH) as f:
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


class AdvancedStatsFunction(object):
    """
    A wrapper for functions and methods that will be executed at regular
    intervals storing the return values for statistic purpose.
    It is possible to provide a custom time function 'timefn' that provides
    cached values to reduce system calls.
    """
    def __init__(self, function, interval=1, window=0, timefn=time.time):
        self._function = function
        self._window = window
        self._timefn = timefn
        self._sample = []

        if not isinstance(interval, int) or interval < 1:
            raise ValueError("interval must be int and greater than 0")

        self._interval = interval

    @property
    def interval(self):
        return self._interval

    def __repr__(self):
        return "<AdvancedStatsFunction %s at 0x%x>" % (
            self._function.__name__, id(self._function.__name__))

    def __call__(self, *args, **kwargs):
        retValue = self._function(*args, **kwargs)
        retTime = self._timefn()

        if self._window > 0:
            self._sample.append((retTime, retValue))
            del self._sample[:-self._window]

        return retValue

    def getStats(self):
        """
        Return a tuple in the format: (first, last, difftime), containing
        the first and the last return value in the defined 'window' and the
        time difference.
        """
        if len(self._sample) < 2:
            return None, None, None

        bgn_time, bgn_sample = self._sample[0]
        end_time, end_sample = self._sample[-1]

        return bgn_sample, end_sample, (end_time - bgn_time)

    def getLastSample(self):
        """
        Return the last collected sample.
        """
        if not self._sample:
            return None
        last_time, last_sample = self._sample[-1]
        return last_sample


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
                            self._log.error("Stats function failed: %s",
                                            statsFunction, exc_info=True)

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
                    self._log.error("Timeout while sampling stats",
                                    exc_info=True)
                self._stopEvent.wait(self.SAMPLE_INTERVAL_SEC)
        except:
            if not self._stopEvent.isSet():
                self._log.error("Error while sampling stats", exc_info=True)

    @utils.memoized
    def _boot_time(self):
        # Try to get boot time only once, if N/A just log the error and never
        # include it in the response.
        try:
            return getBootTime()
        except (IOError, ValueError):
            self._log.error('Failed to get boot time', exc_info=True)
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
