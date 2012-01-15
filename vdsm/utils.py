#
# Copyright 2008-2011 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

"""
A module containing miscellaneous functions and classes that are user plentifuly around vdsm.

.. attribute:: utils.symbolerror

    Contains a reverse dictionary pointing from error string to its error code.
"""
from SimpleXMLRPCServer import SimpleXMLRPCServer
import SocketServer
import threading
import os, traceback, time
import logging
import errno
import subprocess
import pwd
import fcntl
import functools

import ethtool

import constants
from config import config
import netinfo

_THP_STATE_PATH = '/sys/kernel/mm/transparent_hugepage/enabled'
if not os.path.exists(_THP_STATE_PATH):
    _THP_STATE_PATH = '/sys/kernel/mm/redhat_transparent_hugepage/enabled'

def rmFile(fileToRemove):
    """
    Try to remove a file.

    .. note::
        If the operation fails the function exists silently.
    """
    try:
        os.unlink(fileToRemove)
    except:
        pass

def readMemInfo():
    """
    Parse ``/proc/meminfo`` and return its content as a dictionary.

    For a reason unknown to me, ``/proc/meminfo`` is is sometime
    empty when opened. If that happens, the function retries to open it
    3 times.

    :returns: a dictionary representation of ``/proc/meminfo``
    """
    # FIXME the root cause for these retries should be found and fixed
    tries = 3
    meminfo = {}
    while True:
        tries -= 1
        try:
            lines = []
            lines = file('/proc/meminfo').readlines()
            for line in lines:
                var, val = line.split()[0:2]
                meminfo[var[:-1]] = int(val)
            return meminfo
        except:
            logging.warning(traceback.format_exc())
            logging.warning(lines)
            if tries <= 0:
                raise
            time.sleep(0.1)

#Threaded version of SimpleXMLRPCServer
class SimpleThreadedXMLRPCServer(SocketServer.ThreadingMixIn, SimpleXMLRPCServer):
    allow_reuse_address = True

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
            except IOError, e:
                if e.errno != errno.ENOENT: # silently ignore missing wifi stats
                    logging.debug(traceback.format_exc())
                return 0
            try:
                return int(s)
            except:
                if s != '':
                    logging.warning(traceback.format_exc())
                logging.debug('bad %s: (%s)' % (f, s))
                if not tries:
                    raise

    def readIfaceOperstate(self, ifid):
        """
        Return the operational state of the interface.

        :returns: ``'up'`` if interface is up. ``'down'`` or ``0`` id it's down.
        """
        try:
            flags = ethtool.get_flags(ifid)
        except IOError:
            return '0'
        return 'up' if flags & ethtool.IFF_RUNNING else 'down'

    def __init__(self, ifid):
        self.rx = self.readIfaceStat(ifid, 'rx_bytes')
        self.tx = self.readIfaceStat(ifid, 'tx_bytes')
        self.rxDropped = self.readIfaceStat(ifid, 'rx_dropped')
        self.txDropped = self.readIfaceStat(ifid, 'tx_dropped')
        self.rxErrors = self.readIfaceStat(ifid, 'rx_errors')
        self.txErrors = self.readIfaceStat(ifid, 'tx_errors')
        self.operstate = self.readIfaceOperstate(ifid)


class TotalCpuSample:
    """
    A sample of total CPU consumption.

    The sample is taken at initialization time and can't be updated.
    """
    def __init__(self):
        self.user, userNice, self.sys, self.idle = \
                        map(int, file('/proc/stat').readline().split()[1:5])
        self.user += userNice

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

class BaseSample(TimedSample):
    """
    A sample of the statistics for a process.
    """
    def __init__(self, pid, ifids):
        TimedSample.__init__(self)
        self.interfaces= {}
        for ifid in ifids:
            self.interfaces[ifid] = InterfaceSample(ifid)
        self.pidcpu = PidCpuSample(pid)

class HostSample(BaseSample):
    """
    A sample of host-related statistics.

    Contatins the sate of the host in the time of initailization.
    """
    MONITORED_PATHS = ['/tmp', '/var/log', '/var/log/core', constants.P_VDSM_RUN]

    def _getDiskStats(self):
        d = {}
        for p in self.MONITORED_PATHS:
            free = 0
            try:
                stat = os.statvfs(p)
                free = stat.f_bavail * stat.f_bsize / 2**20
            except:
                pass
            d[p] = {'free': str(free)}
        return d

    def __init__(self, pid, ifids):
        """
        Initialize a HostSample.

        :param pid: The PID of this vdsm host.
        :type pid: int
        :param ifids: The IDs of the interfaces you want to sample.
        :type: list
        """
        BaseSample.__init__(self, pid, ifids)
        self.totcpu = TotalCpuSample()
        meminfo = readMemInfo()
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
                self.thpState = s[s.index('[')+1:s.index(']')]
        except:
            self.thpState = 'never'

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
        return "<AdvancedStatsFunction %s at 0x%x>" % \
                   (self._function.__name__, id(self._function.__name__))

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
        if len(self._sample) < 2: return None, None, None

        bgn_time, bgn_sample = self._sample[0]
        end_time, end_sample = self._sample[-1]

        return bgn_sample, end_sample, (end_time - bgn_time)

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
                thisInterval = statsFunction.interval - \
                               (intervalAccum % statsFunction.interval)
                waitInterval = min(waitInterval, thisInterval)

                if intervalAccum % statsFunction.interval == 0:
                    try:
                        statsFunction()
                    except Exception, e:
                        if not self.handleStatsException(e):
                            self._log.error("Stats function failed: %s",
                                            statsFunction, exc_info=True)

            self._stopEvent.wait(waitInterval)
            intervalAccum = (intervalAccum + waitInterval) % maxInterval

class StatsThread(threading.Thread):
    """
    A thread that periodically checks the stats of interfaces
    """
    AVERAGING_WINDOW = 5
    SAMPLE_INTERVAL_SEC = 2
    MBITTOBYTES = 1000000 / 8
    def __init__(self, log, ifids, ifrates, ifmacs):
        threading.Thread.__init__(self)
        self._log = log
        self._lastCheckTime = 0
        self._stopEvent = threading.Event()
        self._samples = []
        self._ifids = ifids
        self._ifrates = ifrates
        self._ifmacs = ifmacs
        self._ncpus = 1
        self._lineRate = (sum(ifrates) or 1000) * 10**6 / 8 # in bytes-per-second
        self._paused = False
        self._lastSampleTime = time.time()

    def stop(self):
        self._stopEvent.set()

    def pause(self):
        self._paused = True

    def cont(self):
        self._paused = False

    def sample(self): # override
        pass

    def run(self):
        import libvirtvm
        try:
            # wait a bit before starting to sample
            time.sleep(self.SAMPLE_INTERVAL_SEC)
            while not self._stopEvent.isSet():
                if not self._paused:
                    try:
                        sample = self.sample()
                        self._samples.append(sample)
                        self._lastSampleTime = sample.timestamp
                        if len(self._samples) > self.AVERAGING_WINDOW:
                            self._samples.pop(0)
                    except libvirtvm.TimeoutError:
                        self._log.error(traceback.format_exc())
                self._stopEvent.wait(self.SAMPLE_INTERVAL_SEC)
        except:
            if not self._stopEvent.isSet():
                self._log.error(traceback.format_exc())

    def get(self):
        """
        Compile and return a dict containg the stats.

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
        for ifid, ifrate, ifmac in zip(self._ifids, self._ifrates,
                                       self._ifmacs):
            ifrate = ifrate or 1000
            Mbps2Bps = 10**6 / 8
            thisRx = (hs1.interfaces[ifid].rx - hs0.interfaces[ifid].rx) % 2**32
            thisTx = (hs1.interfaces[ifid].tx - hs0.interfaces[ifid].tx) % 2**32
            rxRate = 100.0 * thisRx / interval / ifrate / Mbps2Bps
            txRate = 100.0 * thisTx / interval / ifrate / Mbps2Bps
            if txRate > 100 or rxRate > 100:
                txRate = min(txRate, 100.0)
                rxRate = min(rxRate, 100.0)
                self._log.debug('Rate above 100%%. DEBUG: ifid %s interval: %s thisRx %s thisTx %s samples %s', ifid, interval, thisRx, thisTx, [(hs.timestamp, hs.interfaces[ifid].rx, hs.interfaces[ifid].tx) for hs in self._samples])
            stats['network'][ifid] = {'name': ifid, 'speed': str(ifrate),
                    'rxDropped': str(hs1.interfaces[ifid].rxDropped),
                    'txDropped': str(hs1.interfaces[ifid].txDropped),
                    'rxErrors': str(hs1.interfaces[ifid].rxErrors),
                    'txErrors': str(hs1.interfaces[ifid].txErrors),
                    'state': hs1.interfaces[ifid].operstate,
                    'rxRate': '%.1f' % rxRate,
                    'txRate': '%.1f' % txRate,
                    'macAddr': ifmac,
#                    'interval': str(interval), 'thisRx': str(thisRx), 'thisTx': str(thisTx),
#                    'samples': str([(hs.timestamp, hs.interfaces[ifid].rx, hs.interfaces[ifid].tx) for hs in self._samples])
                    }
            rx += thisRx
            tx += thisTx
            rxDropped += hs1.interfaces[ifid].rxDropped
            txDropped += hs1.interfaces[ifid].txDropped
        stats['rxRate'] = 100.0 * rx / interval / self._lineRate
        stats['txRate'] = 100.0 * tx / interval / self._lineRate
        if stats['txRate'] > 100 or stats['rxRate'] > 100:
            stats['txRate'] = min(stats['txRate'], 100.0)
            stats['rxRate'] = min(stats['rxRate'], 100.0)
            logging.debug(stats)
#        stats['rxBps'] = float(rx) / interval
#        stats['txBps'] = float(tx) / interval
        stats['rxDropped'] = rxDropped
        stats['txDropped'] = txDropped

        return stats

class HostStatsThread(StatsThread):
    """
    A thread that periodically samples host statistics.
    """
    def __init__(self, cif, log, ifids, ifrates):
        self.startTime = time.time()
        StatsThread.__init__(self, log, ifids, ifrates,
                             [''] * len(ifids)) # fake ifmacs
        self._imagesStatus = ImagePathStatus(cif)
        self._pid = os.getpid()
        self._ncpus = len(os.listdir('/sys/class/cpuid/'))

    def stop(self):
        self._imagesStatus.stop()
        StatsThread.stop(self)

    def _updateIfRates(self, hs0, hs1):
        # import netinfo only after it imported utils
        from netinfo import speed as nicspeed

        i = 0
        for ifid in self._ifids:
            if hs0.interfaces[ifid].operstate != \
               hs1.interfaces[ifid].operstate:
                self._ifrates[i] = nicspeed(ifid)
            i += 1

    def sample(self):
        hs = HostSample(self._pid, self._ifids)
        if self._samples:
            self._updateIfRates(self._samples[-1], hs)
        return hs

    def get(self):
        stats = StatsThread.get(self)
        stats['cpuSysVdsmd'] = stats['cpuUserVdsmd'] = 0.0
        stats['storageDomains'] = {}
        if self._imagesStatus._cif.irs:
            self._imagesStatus._refreshStorageDomains()
        now = time.time()
        for sd, d in self._imagesStatus.storageDomains.iteritems():
            stats['storageDomains'][sd] = {'code': d['code'],
                        'delay': d['delay'],
                        'lastCheck': '%.1f' % (now - d['lastCheck']),
                        'valid': d['valid']}
        stats['elapsedTime'] = int(now - self.startTime)
        if len(self._samples) < 2:
            return stats
        hs0, hs1 = self._samples[0], self._samples[-1]
        interval = hs1.timestamp - hs0.timestamp
        jiffies = (hs1.pidcpu.user - hs0.pidcpu.user) % 2**32
        stats['cpuUserVdsmd'] = (jiffies / interval) % 2**32
        jiffies = hs1.pidcpu.sys - hs0.pidcpu.sys
        stats['cpuSysVdsmd'] = (jiffies / interval) % 2**32

        jiffies = (hs1.totcpu.user - hs0.totcpu.user) % 2**32
        stats['cpuUser'] = jiffies / interval / self._ncpus
        jiffies = (hs1.totcpu.sys - hs0.totcpu.sys) % 2**32
        stats['cpuSys'] = jiffies / interval / self._ncpus
        stats['cpuIdle'] = max(0.0,
                 100.0 - stats['cpuUser'] - stats['cpuSys'])
        stats['memUsed'] = hs1.memUsed
        stats['anonHugePages'] = hs1.anonHugePages
        stats['cpuLoad'] = hs1.cpuLoad

        stats['diskStats'] = hs1.diskStats
        stats['thpState'] = hs1.thpState
        return stats

def convertToStr (val):
    varType = type(val)
    if varType is float:
        return '%.2f' % (val)
    elif varType is int:
        return '%d' % (val)
    else:
        return val

def execCmd(*args, **kwargs):
    # import only after config as been initialized
    from storage.misc import execCmd
    return execCmd(*args, **kwargs)

def checkPathStat(pathToCheck):
    try:
        startTime = time.time()
        os.statvfs(pathToCheck)
        delay = time.time() - startTime
        return (True, delay)
    except:
        return (False, 0)

class ImagePathStatus(threading.Thread):
    def __init__ (self, cif, interval=None):
        if interval is None:
            interval = config.getint('irs', 'images_check_times')
        self._interval = interval
        self._cif = cif
        self.storageDomains = {}
        self._stopEvent = threading.Event()
        threading.Thread.__init__(self, name='ImagePathStatus')
        if self._interval > 0:
            self.start()

    def stop (self):
        self._stopEvent.set()

    def _refreshStorageDomains(self):
        self.storageDomains = self._cif.irs.repoStats()
        del self.storageDomains["status"]
        if "args" in self.storageDomains:
            del self.storageDomains["args"]

    def run (self):
        try:
            while not self._stopEvent.isSet():
                if self._cif.irs:
                    self._refreshStorageDomains()
                self._stopEvent.wait(self._interval)
        except:
            logging.error(traceback.format_exc())

def getPidNiceness(pid):
    """
    Get the nice level of a process.

    :param pid: the PID of the process.
    :type pid: int
    """
    stat = file('/proc/%s/stat' % (pid)).readlines()[0]
    return int(stat.split(') ')[-1].split()[16])

def tobool(s):
    try:
        if s == None:
            return False
        if type(s) == bool:
            return s
        if s.lower() == 'true':
            return True
        return bool(int(s))
    except:
        return False


__hostUUID = ''
def getHostUUID():
    global __hostUUID
    if __hostUUID:
        return __hostUUID

    __hostUUID = 'None'

    try:
        p = subprocess.Popen([constants.EXT_SUDO,
                              constants.EXT_DMIDECODE, "-s", "system-uuid"],
                             close_fds=True, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate()
        out = '\n'.join( line for line in out.splitlines()
                         if not line.startswith('#') )

        if p.returncode == 0 and 'Not' not in out:
            #Avoid error string - 'Not Settable' or 'Not Present'
            __hostUUID = out.strip()
        else:
            logging.warning('Could not find host UUID.')

        nics = netinfo.get()['nics']
        try:
            mac = sorted([v['hwaddr'] for v in nics.itervalues()])[0]
        except:
            mac = ""
            logging.warning('Could not find host MAC.', exc_info=True)

        if __hostUUID != "None":
            __hostUUID += "_" + mac
        else:
            __hostUUID = "_" + mac
    except:
        logging.error(traceback.format_exc())

    return __hostUUID

symbolerror = {}
for code, symbol in errno.errorcode.iteritems():
    symbolerror[os.strerror(code)] = symbol

def getUserPermissions(userName, path):
    """
    Return a dictionary with user specific permissions with respect to the
    given file
    """
    def isRead(bits):
        return (bits & 4) is not 0

    def isWrite(bits):
        return (bits & 2) is not 0

    def isExec(bits):
        return (bits & 1) is not 0

    fileStats = os.stat(path)
    userInfo = pwd.getpwnam(userName)
    permissions = {}
    otherBits = fileStats.st_mode
    groupBits = otherBits >> 3
    ownerBits = groupBits >> 3
    # TODO: Don't ignore user's auxiliary groups
    isSameGroup = userInfo.pw_gid == fileStats.st_gid
    isSameOwner = userInfo.pw_uid == fileStats.st_uid

    # 'Other' permissions are the base permissions
    permissions['read'] = isRead(otherBits) or \
            isSameGroup and isRead(groupBits) or \
            isSameOwner and isRead(ownerBits)

    permissions['write'] = isWrite(otherBits) or \
            isSameGroup and isWrite(groupBits) or \
            isSameOwner and isWrite(ownerBits)

    permissions['exec'] = isExec(otherBits) or \
            isSameGroup and isExec(groupBits) or \
            isSameOwner and isExec(ownerBits)

    return permissions

def listSplit(l, elem, maxSplits=None):
    splits = []
    splitCount = 0

    while True:
        try:
            splitOffset = l.index(elem)
        except ValueError:
            break

        splits.append( l[:splitOffset] )
        l = l[splitOffset+1:]
        splitCount +=1
        if maxSplits is not None and splitCount >= maxSplits:
            break

    return splits + [l]

def listJoin(elem, *lists):
    if lists == []:
        return []
    l = list(lists[0])
    for i in lists[1:]:
        l += [elem] + i
    return l

def closeOnExec(fd):
    old = fcntl.fcntl(fd, fcntl.F_GETFD, 0)
    fcntl.fcntl(fd, fcntl.F_SETFD, old | fcntl.FD_CLOEXEC)

class memoized(object):
    """Decorator that caches a function's return value each time it is called.
    If called later with the same arguments, the cached value is returned, and
    not re-evaluated. There is no support for uncachable arguments.

    Adaptation from http://wiki.python.org/moin/PythonDecoratorLibrary#Memoize

    """
    def __init__(self, func):
        self.func = func
        self.cache = {}
        functools.update_wrapper(self, func)
    def __call__(self, *args):
        try:
            return self.cache[args]
        except KeyError:
            value = self.func(*args)
            self.cache[args] = value
            return value
    def __get__(self, obj, objtype):
        """Support instance methods."""
        return functools.partial(self.__call__, obj)
