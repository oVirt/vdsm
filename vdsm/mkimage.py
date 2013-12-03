#
# Copyright 2012 Red Hat, Inc.
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

import os
import tempfile
import shutil
import base64
import errno
import hashlib

from vdsm.constants import EXT_MKFS_MSDOS, EXT_MKISOFS, \
    DISKIMAGE_USER, DISKIMAGE_GROUP
from vdsm.constants import P_VDSM_RUN
from vdsm.utils import execCmd
from storage.fileUtils import resolveUid, resolveGid
import storage.mount

_P_PAYLOAD_IMAGES = os.path.join(P_VDSM_RUN, 'payload')


def _openFile(filename, mode, perms):
    '''
    opens a filename allowing to specify the unix permissions
    right from the start, to avoid world-readable files
    with sensitive informations.
    '''
    fd = os.open(filename, os.O_CREAT | os.O_TRUNC | os.O_RDWR, perms)
    return os.fdopen(fd, mode)


def _decodeFilesIntoDir(files, parentdir):
    '''
    create temp files from files list

    make temp file from tempdir/filename and write the content
    to the temp file, the content is base64 string.

    :param files: [{'filename': 'content' ...}]
    :returns: temp dir that store the temp files
    '''

    for name, content in files.iteritems():
        filename = os.path.join(parentdir, name)
        dirname = os.path.dirname(filename)
        if not os.path.exists(dirname):
            try:
                os.makedirs(dirname)
            except OSError as e:
                if e.errno != os.errno.EEXIST:
                    raise
        with _openFile(filename, 'w', 0o640) as f:
            f.write(base64.b64decode(content))


def _commonCleanFs(dirname, media):
    if media is not None:
        os.chown(media, resolveUid(DISKIMAGE_USER),
                 resolveGid(DISKIMAGE_GROUP))

    if dirname is not None:
        shutil.rmtree(dirname)


def _getFileName(vmId, files):
    if not os.path.exists(_P_PAYLOAD_IMAGES):
        try:
            os.mkdir(_P_PAYLOAD_IMAGES)
        except OSError as e:
            if e.errno != os.errno.EEXIST:
                raise
    content = ''.join(files.keys()) + ''.join(files.values())
    md5 = hashlib.md5(content).hexdigest()
    path = os.path.join(_P_PAYLOAD_IMAGES, "%s.%s.img" % (vmId, md5))
    return path


def mkFloppyFs(vmId, files, volumeName=None):
    floppy = dirname = None
    try:
        floppy = _getFileName(vmId, files)
        command = [EXT_MKFS_MSDOS, '-C', floppy, '1440']
        if volumeName is not None:
            command.extend(['-n', volumeName])
        rc, out, err = execCmd(command, raw=True)
        if rc:
            raise OSError(errno.EIO, "could not create floppy file: "
                          "code %s, out %s\nerr %s" % (rc, out, err))

        dirname = tempfile.mkdtemp()
        m = storage.mount.Mount(floppy, dirname)
        m.mount(mntOpts='loop')
        try:
            _decodeFilesIntoDir(files, dirname)
        finally:
            m.umount(force=True, freeloop=True)
    finally:
        _commonCleanFs(dirname, floppy)

    return floppy


def mkIsoFs(vmId, files, volumeName=None):
    dirname = isopath = None
    try:
        dirname = tempfile.mkdtemp()
        _decodeFilesIntoDir(files, dirname)
        isopath = _getFileName(vmId, files)

        command = [EXT_MKISOFS, '-R', '-o', isopath]
        if volumeName is not None:
            command.extend(['-V', volumeName])
        command.extend([dirname])
        rc, out, err = execCmd(command, raw=True, childUmask=0o027)
        if rc:
            raise OSError(errno.EIO, "could not create iso file: "
                          "code %s, out %s\nerr %s" % (rc, out, err))
    finally:
        _commonCleanFs(dirname, isopath)

    return isopath


def removeFs(path):
    if not os.path.abspath(path).startswith(_P_PAYLOAD_IMAGES):
        raise Exception('Cannot remove Fs that does not exists in: ' +
                        _P_PAYLOAD_IMAGES)
    if os.path.exists(path):
        os.remove(path)
