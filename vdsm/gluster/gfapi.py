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
    if fs is None:
        raise ge.GlfsInitException(
            err=['glfs_new(%s) failed' % volumeId]
        )

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
def volumeStatvfsGet(volumeId, host=GLUSTER_VOL_HOST,
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
from vdsm import utils


@makePublic
def volumeStatvfs(volumeName, host=GLUSTER_VOL_HOST,
                  port=GLUSTER_VOL_PORT,
                  protocol=GLUSTER_VOL_PROTOCAL):
    module = "gluster.gfapi"
    command = [constants.EXT_PYTHON, '-m', module, '-v', volumeName,
               '-p', str(port), '-H', host, '-t', protocol]

    # to include /usr/share/vdsm in python path
    env = os.environ.copy()
    env['PYTHONPATH'] = "%s:%s" % (
        env.get("PYTHONPATH", ""), constants.P_VDSM)
    env['PYTHONPATH'] = ":".join(map(os.path.abspath,
                                     env['PYTHONPATH'].split(":")))

    rc, out, err = utils.execCmd(command, raw=True, env=env)
    if rc != 0:
        raise ge.GlfsStatvfsException(rc, [out], [err])
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
                        default=GLUSTER_VOL_PROTOCAL, help="protocol")
    args = parser.parse_args()
    return args


if __name__ == '__main__':
    args = parse_cmdargs()
    res = volumeStatvfsGet(args.volume, args.host,
                           int(args.port), args.protocol)
    json.dump({'f_blocks': res.f_blocks, 'f_bfree': res.f_bfree,
               'f_bsize': res.f_bsize, 'f_frsize': res.f_frsize,
               'f_bavail': res.f_bavail, 'f_files': res.f_files,
               'f_ffree': res.f_ffree, 'f_favail': res.f_favail,
               'f_flag': res.f_flag, 'f_namemax': res.f_namemax},
              sys.stdout)
