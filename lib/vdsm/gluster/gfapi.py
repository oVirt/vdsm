#
# Copyright 2014-2016 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import ctypes
import os

from vdsm.gluster import exception as ge

from . import gluster_mgmt_api


GLUSTER_VOL_PROTOCOL = 'tcp'
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


class DirentStruct(ctypes.Structure):
    _fields_ = [
        ("d_ino", ctypes.c_ulong),
        ("d_off", ctypes.c_ulong),
        ("d_reclen", ctypes.c_ushort),
        ("d_type", ctypes.c_char),
        ("d_name", ctypes.c_char * 256),
    ]


def glfsInit(volumeId, host, port, protocol):
    fs = _glfs_new(volumeId.encode('utf-8'))
    if fs is None:
        raise ge.GlfsInitException(
            err=['glfs_new(%s) failed' % volumeId]
        )

    rc = _glfs_set_volfile_server(fs,
                                  protocol.encode('utf-8'),
                                  host.encode('utf-8'),
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


@gluster_mgmt_api
def volumeStatvfsGet(volumeId, host=GLUSTER_VOL_HOST,
                     port=GLUSTER_VOL_PORT,
                     protocol=GLUSTER_VOL_PROTOCOL):
    statvfsdata = StatVfsStruct()

    fs = glfsInit(volumeId, host, port, protocol)
    rc = _glfs_statvfs(fs, GLUSTER_VOL_PATH.encode('utf-8'),
                       ctypes.byref(statvfsdata))
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


def checkVolumeEmpty(volumeId, host=GLUSTER_VOL_HOST,
                     port=GLUSTER_VOL_PORT,
                     protocol=GLUSTER_VOL_PROTOCOL):
    data = ctypes.POINTER(DirentStruct)()
    fs = glfsInit(volumeId, host, port, protocol)

    fd = _glfs_opendir(fs, b"/")

    if fd is None:
        glfsFini(fs, volumeId)
        raise ge.GlusterVolumeEmptyCheckFailedException(
            err=['glfs_opendir() failed'])

    entry = "."
    flag = False

    while entry in [".", "..", ".trashcan"]:
        data = _glfs_readdir(fd)
        if data is None:
            glfsFini(fs, volumeId)
            raise ge.GlusterVolumeEmptyCheckFailedException(
                err=['glfs_readdir() failed'])

        # When there are no more entries in directory _glfs_readdir()
        # will return a null pointer. bool of null pointer will be false
        # Using this to conclude that no more entries in volume.
        if not bool(data):
            flag = True
            break

        entry = data.contents.d_name
    else:
        flag = False

    glfsFini(fs, volumeId)
    return flag


# C function prototypes for using the library gfapi

_lib = ctypes.CDLL("libgfapi.so.0", use_errno=True)

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

_glfs_opendir = ctypes.CFUNCTYPE(ctypes.c_void_p,
                                 ctypes.c_void_p,
                                 ctypes.c_char_p)(('glfs_opendir', _lib))

_glfs_readdir = ctypes.CFUNCTYPE(ctypes.POINTER(DirentStruct),
                                 ctypes.c_void_p)(('glfs_readdir', _lib))


# This is a workaround for memory leak caused by the
# libgfapi(BZ:1093594) used to get volume statistics.
# Memory accumulates every time the api is invoked to
# avoid that, this file is executed as script.This is
# a temporary fix for BZ:1142647. This can be reverted
# back once the memory leak issue is fixed in libgfapi.

import sys
import json
import argparse

from vdsm import constants
from vdsm.common import commands
from vdsm.common import cmdutils


@gluster_mgmt_api
def volumeStatvfs(volumeName, host=GLUSTER_VOL_HOST,
                  port=GLUSTER_VOL_PORT,
                  protocol=GLUSTER_VOL_PROTOCOL):
    module = "vdsm.gluster.gfapi"
    command = [sys.executable, '-m', module, '-v', volumeName,
               '-p', str(port), '-H', host, '-t', protocol, '-c', 'statvfs']

    # to include /usr/share/vdsm in python path
    env = os.environ.copy()
    env['PYTHONPATH'] = "%s:%s" % (
        env.get("PYTHONPATH", ""), constants.P_VDSM)
    env['PYTHONPATH'] = ":".join(map(os.path.abspath,
                                     env['PYTHONPATH'].split(":")))

    try:
        out = commands.run(command, env=env)
    except cmdutils.Error as e:
        raise ge.GlfsStatvfsException(e.rc, [e.err])
    res = json.loads(out)
    return os.statvfs_result((res['f_bsize'],
                              res['f_frsize'],
                              res['f_blocks'],
                              res['f_bfree'],
                              res['f_bavail'],
                              res['f_files'],
                              res['f_ffree'],
                              res['f_favail'],
                              res['f_flag'],
                              res['f_namemax']))


@gluster_mgmt_api
def volumeEmptyCheck(volumeName, host=GLUSTER_VOL_HOST,
                     port=GLUSTER_VOL_PORT,
                     protocol=GLUSTER_VOL_PROTOCOL):
    module = "vdsm.gluster.gfapi"
    command = [sys.executable, '-m', module, '-v', volumeName,
               '-p', str(port), '-H', host, '-t', protocol, '-c', 'readdir']

    # to include /usr/share/vdsm in python path
    env = os.environ.copy()
    env['PYTHONPATH'] = "%s:%s" % (
        env.get("PYTHONPATH", ""), constants.P_VDSM)
    env['PYTHONPATH'] = ":".join(map(os.path.abspath,
                                     env['PYTHONPATH'].split(":")))

    try:
        out = commands.run(command, env=env)
    except cmdutils.Error as e:
        raise ge.GlusterVolumeEmptyCheckFailedException(e.rc, [e.err])
    return out.upper() == "TRUE"


# This file is modified to act as a script which can retrive
# volume statistics using libgfapi. This can be reverted
# after the memory leak issue is resolved in libgfapi.
def parse_cmdargs():
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--volume", action="store", type=str,
                        help="volumeName")
    parser.add_argument("-H", "--host", action="store", type=str,
                        default=GLUSTER_VOL_HOST, help="host name")
    parser.add_argument("-p", "--port", action="store", type=str,
                        default=str(GLUSTER_VOL_PORT), help="port number")
    parser.add_argument("-t", "--protocol", action="store", type=str,
                        default=GLUSTER_VOL_PROTOCOL, help="protocol")
    parser.add_argument("-c", "--command", action="store", type=str,
                        help="command to be executed")
    args = parser.parse_args()
    return args


if __name__ == '__main__':
    args = parse_cmdargs()
    if args.command.upper() == 'STATVFS':
        try:
            res = volumeStatvfsGet(args.volume, args.host,
                                   int(args.port), args.protocol)
        except ge.GlusterException as e:
            sys.stderr.write(str(e))
            sys.exit(1)
        json.dump({'f_blocks': res.f_blocks, 'f_bfree': res.f_bfree,
                   'f_bsize': res.f_bsize, 'f_frsize': res.f_frsize,
                   'f_bavail': res.f_bavail, 'f_files': res.f_files,
                   'f_ffree': res.f_ffree, 'f_favail': res.f_favail,
                   'f_flag': res.f_flag, 'f_namemax': res.f_namemax},
                  sys.stdout)
    elif args.command.upper() == 'READDIR':
        try:
            result = checkVolumeEmpty(args.volume,
                                      args.host,
                                      int(args.port),
                                      args.protocol)
        except ge.GlusterException as e:
            sys.stderr.write(str(e))
            sys.exit(1)

        sys.stdout.write(str(result))
