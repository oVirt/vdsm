#
# Copyright 2011 Red Hat, Inc.
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
from collections import namedtuple
import errno
from os.path import normpath
import re
import os

import constants
import misc

# Common vfs types

VFS_NFS = "nfs"
VFS_NFS4 = "nfs4"
VFS_EXT3 = "ext3"

MountRecord = namedtuple("MountRecord", "fs_spec fs_file fs_vfstype "
                          "fs_mntops fs_freq fs_passno")

_RE_ESCAPE = re.compile(r"\\0\d+")

class MountError(RuntimeError): pass

def _parseFstabPath(path):
    return _RE_ESCAPE.sub(lambda s: chr(int(s.group()[1:], 8)), path)

def _iterMountRecords():
    with open("/proc/mounts", "r") as f:
        for line in f:
            (fs_spec, fs_file, fs_vfstype, fs_mntops,
                    fs_freq, fs_passno) = line.split()[:6]
            fs_mntops = fs_mntops.split(",")
            fs_freq = int(fs_freq)
            fs_passno = int(fs_passno)
            fs_spec = normpath(_parseFstabPath(fs_spec))
            fs_file = normpath(_parseFstabPath(fs_file))

            yield MountRecord(fs_spec, fs_file, fs_vfstype, fs_mntops,
                    fs_freq, fs_passno)

def iterMounts():
    for record in _iterMountRecords():
        yield Mount(record.fs_spec, record.fs_file)

def isMounted(target):
    """Checks if a target is mounted at least once"""
    try:
        getMountFromTarget(target)
        return True
    except OSError as ex:
        if ex.errno == errno.ENOENT:
            return False
        raise

def getMountFromTarget(target):
    target = normpath(target)
    for rec in _iterMountRecords():
        if rec.fs_file == target:
            return Mount(rec.fs_spec, rec.fs_file)

    raise OSError(errno.ENOENT, 'Mount target %s not found' % target)

class Mount(object):
    def __init__(self, fs_spec, fs_file):
        self.fs_spec = normpath(fs_spec)
        self.fs_file = normpath(fs_file)

    def __eq__(self, other):
        if not isinstance(other, Mount):
            return False

        try:
            return (other.fs_spec == self.fs_spec and
                    other.fs_file == self.fs_file)
        except Exception:
            return False

    def __hash__(self):
        hsh = hash(type(self))
        hsh ^= hash(self.fs_spec)
        hsh ^= hash(self.fs_file)
        return hsh

    def mount(self, mntOpts=None, vfstype=None, timeout=None):
        cmd = [constants.EXT_MOUNT]

        if vfstype is not None:
            cmd.extend(("-t", vfstype))

        if mntOpts:
            cmd.extend(("-o", mntOpts))

        cmd.extend((self.fs_spec, self.fs_file))

        return self._runcmd(cmd, timeout)


    def _runcmd(self, cmd, timeout):
        p = misc.execCmd(cmd, sudo=True, sync=False)
        if not p.wait(timeout):
            p.kill()
            raise OSError(errno.ETIMEDOUT, "%s operation timed out" % os.path.basename(cmd[0]))

        out, err = p.communicate()
        rc = p.returncode

        if rc == 0:
            return

        raise MountError(rc, ";".join((out, err)))

    def umount(self, force=False, lazy=False, timeout=None):
        cmd = [constants.EXT_UMOUNT]
        if force:
            cmd.append("-f")

        if lazy:
            cmd.append("-l")

        cmd.append(self.fs_file)

        return self._runcmd(cmd, timeout)

    def isMounted(self):
        try:
            self.getRecord()
        except OSError:
            return False

        return True

    def getRecord(self):
        for record in _iterMountRecords():
            if (record.fs_spec == self.fs_spec and
                    record.fs_file == self.fs_file):
                return record

        raise OSError(errno.ENOENT,
                "Mount of `%s` at `%s` does not exist" %
                (self.fs_spec, self.fs_file))

    def __repr__(self):
        return "<Mount fs_spec='%s' fs_file='%s'>" % (self.fs_spec, self.fs_file)
