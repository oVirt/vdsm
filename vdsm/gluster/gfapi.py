#
# Copyright 2014 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
import ctypes
from ctypes.util import find_library
import os

import exception as ge
from . import makePublic


GLUSTER_VOL_PROTOCAL = 'tcp'
GLUSTER_VOL_HOST = 'localhost'
GLUSTER_VOL_PORT = 24007
GLUSTER_VOL_PATH = "/"


class StatVfsStruct(ctypes.Structure):
    _fields_ = [
        ('f_bsize', ctypes.c_ulong),
        ('f_frsize', ctypes.c_ulong),
        ('f_blocks', ctypes.c_ulong),
        ('f_bfree', ctypes.c_ulong),
        ('f_bavail', ctypes.c_ulong),
        ('f_files', ctypes.c_ulong),
        ('f_ffree', ctypes.c_ulong),
        ('f_favail', ctypes.c_ulong),
        ('f_fsid', ctypes.c_ulong),
        ('f_flag', ctypes.c_ulong),
        ('f_namemax', ctypes.c_ulong),
        ('__f_spare', ctypes.c_int * 6),
    ]


def glfsInit(volumeId, host, port, protocol):
    fs = _glfs_new(volumeId)

    rc = _glfs_set_volfile_server(fs,
                                  protocol,
                                  host,
                                  port)
    if rc != 0:
        raise ge.GlfsInitException(
            rc=rc, err=["setting volfile server failed"]
        )

    rc = _glfs_init(fs)
    if rc == 0:
        return fs
    elif rc == 1:
        raise ge.GlfsInitException(
            rc=rc, err=["Volume:%s is stopped." % volumeId]
        )
    elif rc == -1:
        raise ge.GlfsInitException(
            rc=rc, err=["Volume:%s not found." % volumeId]
        )
    else:
        raise ge.GlfsInitException(rc=rc, err=["unknown error."])


def glfsFini(fs, volumeId):
    rc = _glfs_fini(fs)
    if rc != 0:
        raise ge.GlfsFiniException(rc=rc)


@makePublic
def volumeStatvfs(volumeId, host=GLUSTER_VOL_HOST,
                  port=GLUSTER_VOL_PORT,
                  protocol=GLUSTER_VOL_PROTOCAL):
    statvfsdata = StatVfsStruct()

    fs = glfsInit(volumeId, host, port, protocol)

    rc = _glfs_statvfs(fs, GLUSTER_VOL_PATH, ctypes.byref(statvfsdata))
    if rc != 0:
        raise ge.GlfsStatvfsException(rc=rc)

    glfsFini(fs, volumeId)

    # To convert to os.statvfs_result we need to pass tuple/list in
    # following order: bsize, frsize, blocks, bfree, bavail, files,
    #                  ffree, favail, flag, namemax
    return os.statvfs_result((statvfsdata.f_bsize,
                              statvfsdata.f_frsize,
                              statvfsdata.f_blocks,
                              statvfsdata.f_bfree,
                              statvfsdata.f_bavail,
                              statvfsdata.f_files,
                              statvfsdata.f_ffree,
                              statvfsdata.f_favail,
                              statvfsdata.f_flag,
                              statvfsdata.f_namemax))

# C function prototypes for using the library gfapi

_lib = ctypes.CDLL(find_library("gfapi"),
                   use_errno=True)

_glfs_new = ctypes.CFUNCTYPE(
    ctypes.c_void_p, ctypes.c_char_p)(('glfs_new', _lib))

_glfs_set_volfile_server = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_int)(('glfs_set_volfile_server', _lib))

_glfs_init = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p)(('glfs_init', _lib))

_glfs_fini = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p)(('glfs_fini', _lib))

_glfs_statvfs = ctypes.CFUNCTYPE(ctypes.c_int,
                                 ctypes.c_void_p,
                                 ctypes.c_char_p,
                                 ctypes.c_void_p)(('glfs_statvfs', _lib))
