#
# Copyright 2008-2013 Red Hat, Inc.
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
from SimpleXMLRPCServer import SimpleXMLRPCServer
import SocketServer
import threading
import os
import time
import logging
import errno
import subprocess
import pwd
import fcntl
import functools
import stat
import glob


import constants
from config import config

_THP_STATE_PATH = '/sys/kernel/mm/transparent_hugepage/enabled'
if not os.path.exists(_THP_STATE_PATH):
    _THP_STATE_PATH = '/sys/kernel/mm/redhat_transparent_hugepage/enabled'


def isBlockDevice(path):
    path = os.path.abspath(path)
    return stat.S_ISBLK(os.stat(path).st_mode)


def rmFile(fileToRemove):
    """
    Try to remove a file.

    If the file doesn't exist is assumed that it was already removed.
    """
    try:
        os.unlink(fileToRemove)
    except OSError as e:
        if e.errno == errno.ENOENT:
            logging.warning("File: %s already removed", fileToRemove)
        else:
            logging.error("Removing file: %s failed", fileToRemove,
                          exc_info=True)
            raise


#Threaded version of SimpleXMLRPCServer
class SimpleThreadedXMLRPCServer(SocketServer.ThreadingMixIn,
                                 SimpleXMLRPCServer):
    allow_reuse_address = True


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
            logging.warning(lines, exc_info=True)
            if tries <= 0:
                raise
            time.sleep(0.1)


def pidStat(pid):
    res = []
    with open("/proc/%d/stat" % pid, "r") as f:
        statline = f.readline()
        procNameStart = statline.find("(")
        procNameEnd = statline.rfind(")")
        res.append(int(statline[:procNameStart]))
        res.append(statline[procNameStart + 1:procNameEnd])
        args = statline[procNameEnd + 2:].split()
        res.append(args[0])
        res.extend([int(item) for item in args[1:]])
        return tuple(res)


def convertToStr(val):
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
    def __init__(self, cif, interval=None):
        if interval is None:
            interval = config.getint('irs', 'images_check_times')
        self._interval = interval
        self._cif = cif
        self.storageDomains = {}
        self._stopEvent = threading.Event()
        threading.Thread.__init__(self, name='ImagePathStatus')
        if self._interval > 0:
            self.start()

    def stop(self):
        self._stopEvent.set()

    def _refreshStorageDomains(self):
        self.storageDomains = self._cif.irs.repoStats()
        del self.storageDomains["status"]
        if "args" in self.storageDomains:
            del self.storageDomains["args"]

    def run(self):
        try:
            while not self._stopEvent.isSet():
                if self._cif.irs:
                    self._refreshStorageDomains()
                self._stopEvent.wait(self._interval)
        except:
            logging.error("Error while refreshing storage domains",
                          exc_info=True)


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
        if s is None:
            return False
        if type(s) == bool:
            return s
        if s.lower() == 'true':
            return True
        return bool(int(s))
    except:
        return False


def _getAllMacs():

    # (
    #     find /sys/class/net/*/device | while read f; do \
    #         cat "$(dirname "$f")/address"; \
    #     done; \
    #     [ -d /proc/net/bonding ] && \
    #         find /proc/net/bonding -type f -exec cat '{}' \; | \
    #         grep 'Permanent HW addr:' | \
    #         sed 's/.* //'
    # ) | sed -e '/00:00:00:00/d' -e '/^$/d'

    macs = []
    for b in glob.glob('/sys/class/net/*/device'):
        mac = file(os.path.join(os.path.dirname(b), "address")). \
            readline().replace("\n", "")
        macs.append(mac)

    for b in glob.glob('/proc/net/bonding/*'):
        for line in file(b):
            if line.startswith("Permanent HW addr: "):
                macs.append(line.split(": ")[1].replace("\n", ""))

    return set(macs) - set(["", "00:00:00:00:00:00"])

__hostUUID = ''


def getHostUUID():
    global __hostUUID
    if __hostUUID:
        return __hostUUID

    __hostUUID = 'None'

    try:
        if os.path.exists(constants.P_VDSM_NODE_ID):
            with open(constants.P_VDSM_NODE_ID) as f:
                __hostUUID = f.readline().replace("\n", "")
        else:
            p = subprocess.Popen([constants.EXT_SUDO,
                                  constants.EXT_DMIDECODE, "-s",
                                  "system-uuid"],
                                 close_fds=True, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            out, err = p.communicate()
            out = '\n'.join(line for line in out.splitlines()
                            if not line.startswith('#'))

            if p.returncode == 0 and 'Not' not in out:
                #Avoid error string - 'Not Settable' or 'Not Present'
                __hostUUID = out.strip()
            else:
                logging.warning('Could not find host UUID.')

            try:
                mac = sorted(_getAllMacs())[0]
            except:
                mac = ""
                logging.warning('Could not find host MAC.', exc_info=True)

            if __hostUUID != "None":
                __hostUUID += "_" + mac
            else:
                __hostUUID = "_" + mac
    except:
        logging.error("Error retrieving host UUID", exc_info=True)

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
    permissions['read'] = (isRead(otherBits) or
                           isSameGroup and isRead(groupBits) or
                           isSameOwner and isRead(ownerBits))

    permissions['write'] = (isWrite(otherBits) or
                            isSameGroup and isWrite(groupBits) or
                            isSameOwner and isWrite(ownerBits))

    permissions['exec'] = (isExec(otherBits) or
                           isSameGroup and isExec(groupBits) or
                           isSameOwner and isExec(ownerBits))

    return permissions


def listSplit(l, elem, maxSplits=None):
    splits = []
    splitCount = 0

    while True:
        try:
            splitOffset = l.index(elem)
        except ValueError:
            break

        splits.append(l[:splitOffset])
        l = l[splitOffset + 1:]
        splitCount += 1
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


def validateMinimalKeySet(dictionary, reqParams):
    if not all(key in dictionary for key in reqParams):
        raise ValueError


class CommandPath(object):
    def __init__(self, name, *args):
        self.name = name
        self.paths = args
        self._cmd = None

    @property
    def cmd(self):
        if not self._cmd:
            for path in self.paths:
                if os.path.exists(path):
                    self._cmd = path
                    break
            else:
                raise OSError(os.errno.ENOENT,
                              os.strerror(os.errno.ENOENT) + ': ' + self.name)
        return self._cmd

    def __repr__(self):
        return str(self.cmd)

    def __str__(self):
        return str(self.cmd)

    def __unicode__(self):
        return unicode(self.cmd)


class PollEvent(object):
    def __init__(self):
        self._r, self._w = os.pipe()
        self._lock = threading.Lock()
        self._isSet = False

    def fileno(self):
        return self._r

    def set(self):
        with self._lock:
            if self._isSet:
                return

            while True:
                try:
                    os.write(self._w, "a")
                    break
                except (OSError, IOError) as e:
                    if e.errno not in (errno.EINTR, errno.EAGAIN):
                        raise

            self._isSet = True

    def isSet(self):
        return self._isSet

    def clear(self):
        with self._lock:
            if not self._isSet:
                return

            while True:
                try:
                    os.read(self._r, 1)
                    break
                except (OSError, IOError) as e:
                    if e.errno not in (errno.EINTR, errno.EAGAIN):
                        raise
            self._isSet = False

    def __del__(self):
        os.close(self._r)
        os.close(self._w)


def retry(func, expectedException=Exception, tries=None,
          timeout=None, sleep=1, stopCallback=None):
    """
    Retry a function. Wraps the retry logic so you don't have to
    implement it each time you need it.

    :param func: The callable to run.
    :param expectedException: The exception you expect to receive when the
                              function fails.
    :param tries: The number of time to try. None\0,-1 means infinite.
    :param timeout: The time you want to spend waiting. This **WILL NOT** stop
                    the method. It will just not run it if it ended after the
                    timeout.
    :param sleep: Time to sleep between calls in seconds.
    :param stopCallback: A function that takes no parameters and causes the
                         method to stop retrying when it returns with a
                         positive value.
    """
    if tries in [0, None]:
        tries = -1

    if timeout in [0, None]:
        timeout = -1

    startTime = time.time()

    while True:
        tries -= 1
        try:
            return func()
        except expectedException:
            if tries == 0:
                raise

            if (timeout > 0) and ((time.time() - startTime) > timeout):
                raise

            if stopCallback is not None and stopCallback():
                raise

            time.sleep(sleep)
