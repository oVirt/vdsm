#
# Copyright 2012-2019 Red Hat, Inc.
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

import logging
import os
import tempfile
import shutil
import stat
import base64
import errno
import hashlib

import six

from vdsm.constants import EXT_MKFS_MSDOS, EXT_MKISOFS, \
    DISKIMAGE_USER, DISKIMAGE_GROUP
from vdsm.constants import P_VDSM_RUN
from vdsm.common.commands import execCmd
from vdsm.common.fileutils import rm_file
from vdsm.storage import mount
from vdsm.storage.fileUtils import resolveUid, resolveGid

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

    for name, content in six.viewitems(files):
        filename = os.path.join(parentdir, name)
        dirname = os.path.dirname(filename)
        if not os.path.exists(dirname):
            try:
                os.makedirs(dirname)
            except OSError as e:
                if e.errno != os.errno.EEXIST:
                    raise
        with _openFile(filename, 'wb', 0o640) as f:
            f.write(base64.b64decode(content))


def _commonCleanFs(dirname, media):
    if media is not None:
        os.chown(media, resolveUid(DISKIMAGE_USER),
                 resolveGid(DISKIMAGE_GROUP))

    if dirname is not None:
        shutil.rmtree(dirname)


def getFileName(vmId, files):
    if not os.path.exists(_P_PAYLOAD_IMAGES):
        try:
            os.mkdir(_P_PAYLOAD_IMAGES)
            os.chown(_P_PAYLOAD_IMAGES,
                     resolveUid(DISKIMAGE_USER),
                     resolveGid(DISKIMAGE_GROUP))
        except OSError as e:
            if e.errno != os.errno.EEXIST:
                raise
    content = ''.join(files.keys()).encode() + b''.join(files.values())
    md5 = hashlib.md5(content).hexdigest()
    path = os.path.join(_P_PAYLOAD_IMAGES, "%s.%s.img" % (vmId, md5))
    return path


def injectFilesToFs(floppy, files, fstype='auto'):
    if not os.path.abspath(floppy).startswith(
            os.path.join(_P_PAYLOAD_IMAGES, '')):
        raise ValueError('Image %s is not inside %s directory' %
                         (floppy, _P_PAYLOAD_IMAGES))
    dirname = None
    try:
        dirname = tempfile.mkdtemp()
        m = mount.Mount(floppy, dirname)
        m.mount(mntOpts='loop', vfstype=fstype)
        try:
            _decodeFilesIntoDir(files, dirname)
        finally:
            m.umount()
    finally:
        _commonCleanFs(dirname, floppy)


def mkFloppyFs(vmId, files, volumeName=None, path=None):
    floppy = None
    try:
        floppy = path or getFileName(vmId, files)
        if os.path.exists(floppy):
            # mkfs.msdos refuses to overwrite existing images
            logging.warning('Removing stale floppy image: %s', floppy)
            rm_file(floppy)
        command = [EXT_MKFS_MSDOS, '-C', floppy, '1440']
        if volumeName is not None:
            command.extend(['-n', volumeName])
        rc, out, err = execCmd(command, raw=True)
        if rc:
            raise OSError(errno.EIO, "could not create floppy file: "
                          "code %s, out %s\nerr %s" % (rc, out, err))
        injectFilesToFs(floppy, files, 'vfat')
    finally:
        _commonCleanFs(None, floppy)

    return floppy


def mkIsoFs(vmId, files, volumeName=None, path=None):
    dirname = isopath = None
    try:
        dirname = tempfile.mkdtemp()
        _decodeFilesIntoDir(files, dirname)
        isopath = path or getFileName(vmId, files)

        command = [EXT_MKISOFS, '-R', '-J', '-o', isopath]
        if volumeName is not None:
            command.extend(['-V', volumeName])
        command.extend([dirname])

        mode = 0o640
        # pre-create the destination iso path with the right permissions;
        # mkisofs/genisoimage will truncate the content and keep the
        # permissions.

        if os.path.exists(isopath):
            logging.warning("iso file %r exists, removing", isopath)
            rm_file(isopath)

        fd = os.open(isopath, os.O_CREAT | os.O_RDONLY | os.O_EXCL, mode)
        os.close(fd)

        rc, out, err = execCmd(command, raw=True)
        if rc:
            # clean up after ourselves in case of error
            removeFs(isopath)
            # skip _commonCleanFs step for missing iso
            isopath = None

            raise OSError(errno.EIO, "could not create iso file: "
                          "code %s, out %s\nerr %s" % (rc, out, err))

        _check_attributes(isopath, mode)

    finally:
        _commonCleanFs(dirname, isopath)

    return isopath


def removeFs(path):
    if not os.path.abspath(path).startswith(_P_PAYLOAD_IMAGES):
        raise Exception('Cannot remove Fs that does not exists in: ' +
                        _P_PAYLOAD_IMAGES)
    if os.path.exists(path):
        os.remove(path)


def _check_attributes(path, mode):
    info = os.stat(path)

    current_mode = stat.S_IMODE(info.st_mode)
    if current_mode != mode:
        logging.warning('wrong mode for %r: expected=%o found=%o',
                        path, mode, current_mode)
