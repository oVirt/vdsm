#
# Copyright 2009-2016 Red Hat, Inc.
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
NFS helper module
"""

from __future__ import absolute_import

import errno
import grp
import logging
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
import time

import selinux
import six

from vdsm import constants
from vdsm.common.network import address
from vdsm.common.osutils import get_umask

log = logging.getLogger('storage.fileUtils')

MIN_PORT = 1
MAX_PORT = 65535


class TarCopyFailed(RuntimeError):
    pass


def tarCopy(src, dst, exclude=()):
    excludeArgs = ["--exclude=%s" % path for path in exclude]

    tsrc = subprocess.Popen([constants.EXT_TAR, "cf", "-"] +
                            excludeArgs + ["-C", src, "."],
                            stdout=subprocess.PIPE)
    tdst = subprocess.Popen([constants.EXT_TAR, "xf", "-", "-C", dst,
                             "--touch"],
                            stdin=tsrc.stdout, stderr=subprocess.PIPE,
                            stdout=subprocess.PIPE)
    tsrc.stdout.close()
    out, err = tdst.communicate()
    tsrc.wait()

    if tdst.returncode != 0 or tsrc.returncode != 0:
        raise TarCopyFailed(tsrc.returncode, tdst.returncode, out, err)


def transformPath(remotePath):
    """
    Transform remote path to new one for local mount
    """
    return remotePath.replace('_', '__').replace('/', '_')


def normalize_path(path):
    """
    Normalizes any path using fileUtils.normpath.
    The input's form can be:
    1. Remote path - "server:port:/path", where:
        - The "port:" part is not mandatory.
        - The "server" part can be a dns name, an ipv4 address
        or an ipv6 address using quoted form.
        - If the input doesn't contain a colon, a HosttailError will be raised.
        Since this format is ambiguous, we treat an input that looks like a
        port as a port, and otherwise as a path without a leading slash.
    2. Local path - "/path/to/device"
    3. Other, where we just call os.path.normpath.
    """
    if path.startswith("/") or ":" not in path:
        return normpath(path)

    host, tail = address.hosttail_split(path)
    if ":" in tail:
        port, path = tail.split(':', 1)
        if is_port(port):
            tail = port + ":" + normpath(path)
        else:
            tail = normpath(tail)
    else:
        tail = normpath(tail)
    return address.hosttail_join(host, tail)


def normpath(path):
    """
    Normalize file system path.

    POSIX allows both /path and //path. The second slash may be interpreted in
    an implementation-defined manner. The Linux interpretation seems to be to
    ignore the double slash, so it seems to be safe to remove it.

    See https://bugs.python.org/issue26329 for more info.
    """
    path = os.path.normpath(path)
    if path.startswith('//'):
        path = path[1:]
    return path


def is_port(port_str):
    if port_str.startswith('0'):
        return False
    try:
        port = int(port_str)
        return MIN_PORT <= port <= MAX_PORT
    except ValueError:
        return False


def validateAccess(targetPath, perms=(os.R_OK | os.W_OK | os.X_OK)):
    """
    Validate the RWX access to a given path
    """
    if not os.access(targetPath, perms):
        log.warning("Permission denied for directory: %s with permissions: %s",
                    targetPath, perms)
        raise OSError(errno.EACCES, os.strerror(errno.EACCES))


def validateQemuReadable(targetPath):
    """
    Validate that qemu process can read file
    """
    gids = (grp.getgrnam(constants.DISKIMAGE_GROUP).gr_gid,
            grp.getgrnam(constants.METADATA_GROUP).gr_gid)
    st = os.stat(targetPath)
    if not (st.st_gid in gids and st.st_mode & stat.S_IRGRP or
            st.st_mode & stat.S_IROTH):
        raise OSError(errno.EACCES, os.strerror(errno.EACCES))


def pathExists(filename, writable=False):
    check = os.R_OK

    if writable:
        check |= os.W_OK

    # This function is workaround for a NFS issue where sometimes
    # os.exists/os.access fails due to NFS stale handle, in such
    # case we need to test os.access a second time.
    if os.access(filename, check):
        return True

    return os.access(filename, check)


def cleanupdir(dirPath, ignoreErrors=True):
    """
    Recursively remove all the files and directories in the given directory
    """
    cleanupdir_errors = []

    def logit(func, path, exc_info):
        cleanupdir_errors.append('%s: %s' % (func.__name__, exc_info[1]))

    log.info("Removing directory: %s", dirPath)
    shutil.rmtree(dirPath, onerror=logit)
    if not ignoreErrors and cleanupdir_errors:
        raise RuntimeError("%s %s" % (dirPath, cleanupdir_errors))


def createdir(dirPath, mode=None):
    """
    Recursively create directory if doesn't exist

    If already exists check that permissions are as requested.
    """
    if mode is not None:
        mode = stat.S_IMODE(mode)
        params = (dirPath, mode)
    else:
        params = (dirPath,)

    log.info("Creating directory: %s mode: %s", dirPath,
             mode if mode is None else oct(mode))
    try:
        os.makedirs(*params)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
        statinfo = os.stat(dirPath)
        if not stat.S_ISDIR(statinfo.st_mode):
            raise OSError(errno.ENOTDIR, "Not a directory %s" % dirPath)
        log.debug("Using existing directory: %s", dirPath)
        if mode is not None:
            actual_mode = stat.S_IMODE(statinfo.st_mode)
            expected_mode = mode & ~get_umask()
            if actual_mode != expected_mode:
                raise OSError(
                    errno.EPERM,
                    "Existing {} permissions {:o} are not as requested"
                    " {:o}".format(dirPath, actual_mode, expected_mode))


def resolveUid(user):
    if isinstance(user, six.string_types):
        uid = pwd.getpwnam(user).pw_uid
    else:
        uid = int(user)
    return uid


def resolveGid(group):
    if isinstance(group, six.string_types):
        gid = grp.getgrnam(group).gr_gid
    else:
        gid = int(group)
    return gid


def chown(path, user=-1, group=-1):
    """
    Change the owner and\or group of a file.
    The user and group parameters can either be a name or an id.
    """
    uid = resolveUid(user)
    gid = resolveGid(group)

    stat = os.stat(path)
    currentUid = stat.st_uid
    currentGid = stat.st_gid

    if ((uid == currentUid or user == -1) and
            (gid == currentGid or group == -1)):
        return True
    log.info("Changing owner for %s, to (%s:%s)", path, uid, gid)
    os.chown(path, uid, gid)
    return True


def backup_file(filename):
    """
    Backup filename with a timestamp and returns the backup filename.
    """
    if os.path.exists(filename):
        backup = filename + '.' + time.strftime("%Y%m%d%H%M%S")
        shutil.copyfile(filename, backup)
        return backup


def atomic_write(filename, data, mode=0o644, relabel=False):
    """
    Write data to filename atomically using a temporary file.

    Arguments:
        filename (str): Path to file.
        data (bytes): Data to write to filename.
        mode (int): Set mode bits on filename.
        relabel (bool): If True, set selinux label.
    """
    with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=os.path.dirname(filename),
            prefix=os.path.basename(filename) + ".tmp",
            delete=False) as tmp:
        try:
            tmp.write(data)
            tmp.flush()

            if relabel:
                # Force required to preserve "system_u". Without it the
                # temporary file will be labeled as "unconfined_u".
                selinux.restorecon(tmp.name, force=True)

            os.chmod(tmp.name, mode)
            os.rename(tmp.name, filename)
        except:
            os.unlink(tmp.name)
            raise


def atomic_symlink(target, name):
    """
    Create s symbolic link atomically, updating stale links.

    If the symlink exists but links to a different target, it is replaced
    atomically with a link to the requested target.

    If the process is killed while creating a link, it may leave temporary link
    (name.tmp). This link will be removed in the next time a link is created.

    This replace a link atomically, so you will have either the old link, or
    the new link. However it is not safe to call this from multiple threads,
    trying to modify the same link.
    """
    log.info("Linking %r to %r", target, name)
    try:
        current_target = os.readlink(name)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise
    else:
        if current_target == target:
            log.debug("link %r exists", name)
            return
        log.debug("Replacing stale link to %r", current_target)

    tmp_name = name + ".tmp"
    try:
        log.debug("Creating symlink from %r to %r", target, tmp_name)
        os.symlink(target, tmp_name)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
        log.info("Removing stale temporary link %r", tmp_name)
        os.unlink(tmp_name)
        log.debug("Creating symlink from %r to %r", target, tmp_name)
        os.symlink(target, tmp_name)

    try:
        log.debug("Renaming %r to %r", tmp_name, name)
        os.rename(tmp_name, name)
    except:
        exc_info = sys.exc_info()
        log.debug("Unlinking %r", tmp_name)
        try:
            os.unlink(tmp_name)
        except OSError as e:
            log.error("Cannot remove temporary link %r: %s", tmp_name, e)
        six.reraise(*exc_info)


def fsyncPath(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
