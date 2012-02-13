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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import tempfile
import shutil
import base64
import errno
import hashlib

from vdsm.constants import EXT_MKFS_MSDOS, EXT_MKISOFS, DISKIMAGE_USER, DISKIMAGE_GROUP
from vdsm.constants import P_VDSM_RUN
from storage.fileUtils import resolveUid, resolveGid
import storage.misc
import storage.mount

_P_PAYLOAD_IMAGES = os.path.join(P_VDSM_RUN, 'payload')

def _decodeFilesIntoDir(files, dirname):
    '''
    create temp files from files list

    make temp file from tempdir/filename and write the content
    to the temp file, the content is base64 string.

    :param files: [{'filename': 'content' ...}]
    :returns: temp dir that store the temp files
    '''

    for name, content in files.iteritems():
        filename = os.path.join(dirname, name)
        with file(filename, 'w') as f: f.write(base64.b64decode(content))

def _commonCleanFs(dirname, media):
    if media is not None:
        os.chown(media, resolveUid(DISKIMAGE_USER),
                resolveGid(DISKIMAGE_GROUP))

    if dirname is not None:
        shutil.rmtree(dirname)

def _getFileName(vmId, files):
    content = ''.join(files.keys()) + ''.join(files.values())
    md5 = hashlib.md5(content).hexdigest()
    path = os.path.join(_P_PAYLOAD_IMAGES, "%s.%s.img" % (vmId, md5))
    return path

def mkFloppyFs(vmId, files):
    try:
        floppy = _getFileName(vmId, files)
        command = [EXT_MKFS_MSDOS, '-C', floppy, '1440']
        rc, out, err = storage.misc.execCmd(command, raw=True)
        if rc:
            raise OSError(errno.EIO, "could not create floppy file: \
                    code %s, out %s\nerr %s" % (rc, out, err))

        dirname = tempfile.mkdtemp()
        m = storage.mount.Mount(floppy, dirname)
        m.mount(mntOpts='loop')
        try:
            _decodeFilesIntoDir(files, dirname)
        finally:
            m.umount(force=True)
    finally:
        _commonCleanFs(dirname, floppy)

    return floppy

def mkIsoFs(vmId, files):
    try:
        dirname = tempfile.mkdtemp()
        _decodeFilesIntoDir(files, dirname)
        isopath = _getFileName(vmId, files)

        command = [EXT_MKISOFS, '-r', '-o', isopath, dirname]
        rc, out, err = storage.misc.execCmd(command, raw=True)
        if rc:
            raise OSError(errno.EIO, "could not create iso file: \
                    code %s, out %s\nerr %s" % (rc, out, err))
    finally:
        _commonCleanFs(dirname, isopath)

    return isopath

def removeFs(path):
    if not os.path.abspath(path).startswith(_P_PAYLOAD_IMAGES):
        raise Exception('Cannot remove Fs that does not exists in: ' + _P_PAYLOAD_IMAGES)
    if os.path.exists(path):
        os.remove(path)
