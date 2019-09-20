#
# Copyright 2011-2016 Red Hat, Inc.
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
import grp
import logging
import os
import stat
import threading
import types
import weakref

from functools import partial
import six

from vdsm import constants
from vdsm import utils
from vdsm.config import config
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage.compat import ioprocess

DEFAULT_TIMEOUT = config.getint("irs", "process_pool_timeout")
IOPROC_IDLE_TIME = config.getint("irs", "max_ioprocess_idle_time")
HELPERS_PER_DOMAIN = config.getint("irs", "process_pool_max_slots_per_domain")
MAX_QUEUED = config.getint("irs", "process_pool_max_queued_slots_per_domain")

_procPoolLock = threading.Lock()
_procPool = {}
_refProcPool = {}

elapsed_time = lambda: os.times()[4]

log = logging.getLogger('storage.oop')


def stop():
    """
    Called during application shutdown to close all running ioprocesses.

    Tests using oop should call this to ensure that stale ioprocess are not
    left when a tests ends.
    """
    with _procPoolLock:
        for name, (eol, proc) in _procPool.items():
            log.debug("Closing ioprocess %s", name)
            try:
                proc._ioproc.close()
            except Exception:
                log.exception("Error closing ioprocess %s", name)
        _procPool.clear()
        _refProcPool.clear()


def cleanIdleIOProcesses(clientName):
    now = elapsed_time()
    for name, (eol, proc) in list(six.iteritems(_procPool)):
        if (eol < now and name != clientName):
            log.debug("Removing idle ioprocess %s", name)
            del _procPool[name]


def getProcessPool(clientName):
    with _procPoolLock:
        cleanIdleIOProcesses(clientName)

        proc = _refProcPool.get(clientName, lambda: None)()
        if proc is None:
            log.debug("Creating ioprocess %s", clientName)
            proc = ioprocess.IOProcess(max_threads=HELPERS_PER_DOMAIN,
                                       timeout=DEFAULT_TIMEOUT,
                                       max_queued_requests=MAX_QUEUED,
                                       name=clientName)
            proc = _IOProcWrapper("oop", proc)
            _refProcPool[clientName] = weakref.ref(proc)

        _procPool[clientName] = (elapsed_time() + IOPROC_IDLE_TIME, proc)
        return proc


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
        newSize = utils.round(size, sc.BLOCK_SIZE_4K)
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
        self._iop.rename(oldpath, newpath)

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
            # Note: islink does not follow symlinks. This is not documented
            # excplicitly, but it deos not make sense otherwise.
            try:
                res = self._iop.lstat(path)
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


def readLines(ioproc, path):
    return ioproc.readlines(path)


def writeLines(ioproc, path, lines):
    data = b''.join(lines)
    return writeFile(ioproc, path, data)


def writeFile(ioproc, path, data, direct=False):
    return ioproc.writefile(path, data, direct=direct)


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
    ioproc.truncate(path, size, mode if mode is not None else 0, creatExcl)
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

        self.readLines = partial(readLines, ioproc)
        self.writeLines = partial(writeLines, ioproc)
        self.writeFile = partial(writeFile, ioproc)
        self.simpleWalk = partial(simpleWalk, ioproc)
        self.truncateFile = partial(truncateFile, ioproc)

    def readFile(self, path, direct=False):
        return self._ioproc.readfile(path, direct=direct)

    def probe_block_size(self, dir_path):
        return self._ioproc.probe_block_size(dir_path)
