#
# Copyright 2011-2014 Red Hat, Inc.
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
import errno
import grp
import logging
import os
import stat
import sys
import types
from warnings import warn
import weakref

from vdsm import constants
from vdsm.config import config
import threading
from functools import partial

try:
    from ioprocess import IOProcess
except ImportError:
    pass

from remoteFileHandler import RemoteFileHandlerPool
import storage_exception as se

RFH = 'rfh'
IOPROC = 'ioprocess'
GLOBAL = 'Global'

_oopImpl = RFH

DEFAULT_TIMEOUT = config.getint("irs", "process_pool_timeout")
IOPROC_IDLE_TIME = config.getint("irs", "max_ioprocess_idle_time")
HELPERS_PER_DOMAIN = config.getint("irs", "process_pool_max_slots_per_domain")
MAX_QUEUED = config.getint("irs", "process_pool_max_queued_slots_per_domain")

_procPoolLock = threading.Lock()
_procPool = {}
_refProcPool = {}
_rfhPool = {}

elapsed_time = lambda: os.times()[4]

log = logging.getLogger('Storage.oop')


def setDefaultImpl(impl):
    global _oopImpl
    _oopImpl = impl
    if impl == IOPROC and IOPROC not in sys.modules:
        log.warning("Cannot import IOProcess, set oop to use RFH")
        _oopImpl = RFH


def cleanIdleIOProcesses(clientName):
    now = elapsed_time()
    for name, (eol, proc) in _procPool.items():
        if (eol < now and name != clientName):
            del _procPool[name]


def _getRfhPool(clientName):
    with _procPoolLock:
        try:
            return _rfhPool[clientName]
        except KeyError:
            _rfhPool[clientName] = _OopWrapper(
                RemoteFileHandlerPool(HELPERS_PER_DOMAIN))

            return _rfhPool[clientName]


def _getIOProcessPool(clientName):
    with _procPoolLock:
        cleanIdleIOProcesses(clientName)

        proc = _refProcPool.get(clientName, lambda: None)()
        if proc is None:
            proc = _OopWrapper(IOProcess(max_threads=HELPERS_PER_DOMAIN,
                                         timeout=DEFAULT_TIMEOUT,
                                         max_queued_requests=MAX_QUEUED))
            _refProcPool[clientName] = weakref.ref(proc)

        _procPool[clientName] = (elapsed_time() + IOPROC_IDLE_TIME, proc)
        return proc


def getProcessPool(clientName):
    if _oopImpl == IOPROC:
        return _getIOProcessPool(clientName)
    else:
        return _getRfhPool(clientName)


def getGlobalProcPool():
    return getProcessPool(GLOBAL)


class _IOProcessGlob(object):
    def __init__(self, iop):
        self._iop = iop

    def glob(self, pattern):
        return self._iop.glob(pattern)


class _IOProcessFileUtils(object):
    def __init__(self, iop):
        self._iop = iop

    def fsyncPath(self, path):
        self._iop.fsyncPath(path)

    def cleanupdir(self, path, ignoreErrors=True):
        cleanupdir_errors = []

        try:
            files = self._iop.listdir(path)
        except OSError:
            if not ignoreErrors:
                raise
        else:
            for f in files:
                fullpath = os.path.join(path, f)
                if _IOProcessOs(self._iop).path.isdir(fullpath):
                    try:
                        self.cleanupdir(fullpath, ignoreErrors)
                    except OSError as e:
                        cleanupdir_errors.append(e)
                else:
                    try:
                        self._iop.unlink(fullpath)
                    except Exception as e:
                        cleanupdir_errors.append('%s: %s' % ("unlink", e))
            try:
                self._iop.rmdir(path)
            except Exception as e:
                cleanupdir_errors.append('%s: %s' % ("rmdir", e))

        if not ignoreErrors and cleanupdir_errors:
            raise se.MiscDirCleanupFailure("%s %s" % (path, cleanupdir_errors))

    def copyUserModeToGroup(self, path):
        mode = _IOProcessOs(self._iop).stat(path).st_mode
        userMode = mode & 0o700  # user mode mask
        newGroupMode = userMode >> 3
        if (mode & 0o070) != newGroupMode:  # group mode mask
            # setting the new group mode masking out the original one
            newMode = (mode & 0o707) | newGroupMode
            log.debug("Changing mode for %s to %#o", path, newMode)
            _IOProcessOs(self._iop).chmod(path, newMode)

    def createdir(self, path, mode=None):
        parts = path.split("/")
        tmpPath = ""
        for part in parts:
            tmpPath = os.path.join(tmpPath, part)
            if tmpPath == "":
                tmpPath = "/"

            try:
                if mode:
                    self._iop.mkdir(tmpPath, mode)
                else:
                    self._iop.mkdir(tmpPath)
            except OSError as e:
                if e.errno != errno.EEXIST:
                    raise
                else:
                    if tmpPath == path and mode is not None:
                        statinfo = self._iop.stat(path)
                        curMode = statinfo[stat.ST_MODE]
                        if curMode != mode:
                            raise OSError(errno.EPERM,
                                          ("Existing %s "
                                           "permissions %s are not as "
                                           "requested %s") % (path,
                                                              oct(curMode),
                                                              oct(mode)))

    def padToBlockSize(self, path):
        size = _IOProcessOs(self._iop).stat(path).st_size
        newSize = 512 * ((size + 511) / 512)
        log.debug("Truncating file %s to %d bytes", path, newSize)
        truncateFile(self._iop, path, newSize)

    def validateAccess(self, targetPath, perms=(os.R_OK | os.W_OK | os.X_OK)):
        if not self._iop.access(targetPath, perms):
            log.warning("Permission denied for directory: %s with permissions:"
                        "%s", targetPath, perms)
            raise OSError(errno.EACCES, os.strerror(errno.EACCES))

    def pathExists(self, filename, writable=False):
        return self._iop.pathExists(filename, writable)

    def validateQemuReadable(self, targetPath):
        """
        Validate that qemu process can read file
        """
        gids = (grp.getgrnam(constants.DISKIMAGE_GROUP).gr_gid,
                grp.getgrnam(constants.METADATA_GROUP).gr_gid)
        st = _IOProcessOs(self._iop).stat(targetPath)
        if not (st.st_gid in gids and st.st_mode & stat.S_IRGRP or
                st.st_mode & stat.S_IROTH):
            raise OSError(errno.EACCES, os.strerror(errno.EACCES))


class _IOProcessOs(object):
    def __init__(self, iop):
        self._iop = iop
        self.path = _IOProcessOs.Path(iop)

    def access(self, path, perms):
        return self._iop.access(path, perms)

    def chmod(self, path, mode):
        self._iop.chmod(path, mode)

    def link(self, src, dst):
        self._iop.link(src, dst)

    def mkdir(self, path, mode=None):
        if mode is not None:
            self._iop.mkdir(path, mode)
        else:
            self._iop.mkdir(path)

    def remove(self, path):
        self._iop.unlink(path)

    def rename(self, oldpath, newpath):
        try:
            return self._iop.rename(oldpath, newpath)
        except OSError as e:
            if e.errno != errno.ENOTEMPTY:
                raise

        warn("Renaming a non-empty directory is not an atomic operation",
             DeprecationWarning)

        _IOProcessFileUtils(self._iop).cleanupdir(newpath, False)
        self.mkdir(newpath)
        for fname in self.listdir(oldpath):
            src = os.path.join(oldpath, fname)
            dst = os.path.join(newpath, fname)
            self.rename(src, dst)

        self._iop.rmdir(oldpath)

    def rmdir(self, path):
        self._iop.rmdir(path)

    def stat(self, path):
        return self._iop.stat(path)

    def statvfs(self, path):
        return self._iop.statvfs(path)

    def unlink(self, path):
        return self._iop.unlink(path)

    class Path(object):
        def __init__(self, iop):
            self._iop = iop

        def isdir(self, path):
            try:
                res = self._iop.stat(path)
            except OSError as e:
                if e.errno == errno.ENOENT:
                    return False
                else:
                    raise
            else:
                return stat.S_ISDIR(res.st_mode)

        def islink(self, path):
            try:
                res = self._iop.stat(path)
            except OSError as e:
                if e.errno == errno.ENOENT:
                    return False
                else:
                    raise
            else:
                return stat.S_ISLNK(res.st_mode)

        def lexists(self, path):
            return self._iop.lexists(path)

        def exists(self, path):
            return self._iop.pathExists(path, False)


class _IOProcessUtils(object):
    def __init__(self, iop):
        self._iop = iop

    def forceLink(self, src, dst):
        """ Makes or replaces a hard link.

        Like os.link() but replaces the link if it exists.
        """
        try:
            _IOProcessOs(self._iop).link(src, dst)
        except OSError as e:
            if e.errno == errno.EEXIST:
                self.rmFile(dst)
                _IOProcessOs(self._iop).link(src, dst)
            else:
                log.error("Linking file: %s to %s failed", src, dst,
                          exc_info=True)
                raise

    def rmFile(self, path):
        """
        Try to remove a file.

        If the file doesn't exist it's assumed that it was already removed.
        """
        try:
            _IOProcessOs(self._iop).unlink(path)
        except OSError as e:
            if e.errno == errno.ENOENT:
                log.warning("File: %s already removed", path)
            else:
                log.error("Removing file: %s failed", path, exc_info=True)
                raise


def directTouch(ioproc, path, mode=0o777):
    flags = os.O_CREAT | os.O_DIRECT
    ioproc.touch(path, flags, mode)


def directReadLines(ioproc, path):
    fileStr = ioproc.readfile(path, direct=True)
    return fileStr.splitlines(True)


def readLines(ioproc, path):
    return ioproc.readlines(path)


def writeLines(ioproc, path, lines):
    data = ''.join(lines)
    return ioproc.writefile(path, data)


def simpleWalk(ioproc, path):
    files = []
    for f in ioproc.listdir(path):
        fullpath = os.path.join(path, f)
        osPath = _IOProcessOs(ioproc).path
        if osPath.isdir(fullpath) and not osPath.islink(fullpath):
            files.extend(simpleWalk(ioproc, fullpath))
        else:
            files.append(fullpath)

    return files


def truncateFile(ioproc, path, size, mode=None, creatExcl=False):
    ioproc.truncate(path, size, mode, creatExcl)
    if mode is not None:
        _IOProcessOs(ioproc).chmod(path, mode)


class _IOProcWrapper(types.ModuleType):
    def __init__(self, modname, ioproc):
        self._modName = modname
        self._ioproc = ioproc

        self.glob = _IOProcessGlob(ioproc)
        self.fileUtils = _IOProcessFileUtils(ioproc)
        self.os = _IOProcessOs(ioproc)
        self.utils = _IOProcessUtils(ioproc)

        self.directReadLines = partial(directReadLines, ioproc)
        self.readLines = partial(readLines, ioproc)
        self.writeLines = partial(writeLines, ioproc)
        self.simpleWalk = partial(simpleWalk, ioproc)
        self.directTouch = partial(directTouch, ioproc)
        self.truncateFile = partial(truncateFile, ioproc)


class _ModuleWrapper(types.ModuleType):
    def __init__(self, modName, procPool, timeout, subModNames=()):
        self._modName = modName
        self._procPool = procPool
        self._timeout = timeout

        for subModName in subModNames:
            subSubModNames = []
            if isinstance(subModName, tuple):
                subModName, subSubModNames = subModName

            fullModName = "%s.%s" % (modName, subModName)

            setattr(self, subModName,
                    _ModuleWrapper(fullModName,
                                   self._procPool,
                                   DEFAULT_TIMEOUT,
                                   subSubModNames)
                    )

    def __getattr__(self, name):
        # Root modules is fake, we need to remove it
        fullName = ".".join(self._modName.split(".")[1:] + [name])

        return partial(self._procPool.callCrabRPCFunction, self._timeout,
                       fullName)


def _OopWrapper(procPool):
    if _oopImpl == IOPROC:
        return _IOProcWrapper("oop", procPool)
    else:
        return _ModuleWrapper("oop", procPool, DEFAULT_TIMEOUT,
                              (("os",
                                ("path",)),
                               "glob",
                               "fileUtils",
                               "utils"))
