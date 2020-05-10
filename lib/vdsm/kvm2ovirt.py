# Copyright 2016-2019 Red Hat, Inc.
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

import argparse
from contextlib import contextmanager
import libvirt
import six
import sys
import os
import threading

# TODO: Stop using internal modules.
from ovirt_imageio._internal import directio

from vdsm.common import concurrent
from vdsm.common import libvirtconnection
from vdsm.common import time
from vdsm.common.password import ProtectedPassword
from vdsm.common.units import MiB

_start = None


class VMAdapter(object):
    def __init__(self, vm, src):
        self._vm = vm
        self._src = src
        self._pos = 0

    def read(self, size):
        buf = self._vm.blockPeek(self._src, self._pos, size)
        self._pos += len(buf)
        return buf

    def finish(self):
        pass


class StreamAdapter(object):
    def __init__(self, stream):
        self.read = stream.recv
        self._stream = stream

    def readinto(self, b):
        # This method is required for `io` module compatibility.
        temp = self.read(len(b))
        temp_len = len(temp)
        if temp_len == 0:
            return 0
        else:
            b[:temp_len] = temp
            return temp_len

    def finish(self):
        self._stream.finish()


class Sparseness(object):
    def __init__(self, opaque, estimated_size):
        self.done = 0
        self.opaque = opaque
        self.estimated_size = estimated_size


def bytesWriteHandler(stream, buf, opaque):
    fd = opaque.opaque
    return os.write(fd, buf)


def recvSkipHandler(stream, length, opaque):
    opaque.done += length
    progress = min(99, opaque.done * 100 // opaque.estimated_size)
    write_progress(progress)
    fd = opaque.opaque
    cur = os.lseek(fd, length, os.SEEK_CUR)
    return os.ftruncate(fd, cur)


def arguments(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('args')
    parser.add_argument('--uri', dest='uri', required=True,
                        help='Libvirt URI')
    parser.add_argument('--username', dest='username', default='',
                        help='Libvirt login user name')
    parser.add_argument('--password-file', dest='password_file', default='',
                        help='Libvirt login password read from a file')
    parser.add_argument('--source', dest='source', nargs='+', required=True,
                        help='Source remote volumes path')
    parser.add_argument('--dest', dest='dest', nargs='+', required=True,
                        help='Destination local volumes path')
    parser.add_argument('--storage-type', dest='storagetype', nargs='+',
                        required=True, help='Storage type (volume or path)')
    parser.add_argument('--vm-name', dest='vmname', required=True,
                        help='Libvirt source VM name')
    parser.add_argument('--bufsize', dest='bufsize', default=MiB,
                        type=int, help='Size of packets in bytes, default'
                        '1048676')
    parser.add_argument('--verbose', action='store_true',
                        help='verbose output')
    parser.add_argument('--allocation', dest='allocation', default='',
                        help='Allocation Policy')

    return parser.parse_args(args)


def write_output(msg):
    sys.stdout.write('[%7.1f] %s\n' % (time.monotonic_time() - _start, msg))
    sys.stdout.flush()


def write_error(e):
    write_output("ERROR: %s" % e)


def write_progress(progress):
    sys.stdout.write('    (%d/100%%)\r' % progress)
    sys.stdout.flush()


def volume_progress(op, done, estimated_size):
    while op.done < estimated_size:
        progress = min(99, op.done * 100 // estimated_size)
        write_progress(progress)
        if done.wait(1):
            break
    write_progress(100)


@contextmanager
def progress(op, estimated_size):
    done = threading.Event()
    th = concurrent.thread(volume_progress, args=(op, done, estimated_size))
    th.start()
    try:
        yield th
    finally:
        done.set()
        th.join()


def download_disk(adapter, estimated_size, size, dest, bufsize):
    op = directio.Receive(dest, adapter, size=size, buffersize=bufsize)
    with progress(op, estimated_size):
        op.run()
    adapter.finish()


def download_disk_sparse(stream, estimated_size, size, dest, bufsize):
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    op = Sparseness(fd, estimated_size)
    with progress(op, estimated_size):
        stream.sparseRecvAll(bytesWriteHandler, recvSkipHandler, op)
    stream.finish()
    os.close(fd)


def get_password(options):
    if not options.password_file:
        return None
    if options.verbose:
        write_output('>>> Reading password from file %s' %
                     options.password_file)
    with open(options.password_file, 'r') as f:
        return ProtectedPassword(f.read())


def handle_volume(con, diskno, src, dst, options):
    write_output('Copying disk %d/%d to %s' % (diskno, len(options.source),
                                               dst))
    vol = con.storageVolLookupByPath(src)
    _, capacity, allocation = vol.info()
    if options.verbose:
        write_output('>>> disk %d, capacity: %d allocation %d' %
                     (diskno, capacity, allocation))

    estimated_size = capacity
    stream = con.newStream()
    preallocated = True

    if options.allocation == "sparse" and \
            con.getLibVersion() >= 3004000:
        try:
            preallocated = False
            vol.download(stream, 0, 0,
                         libvirt.VIR_STORAGE_VOL_DOWNLOAD_SPARSE_STREAM)
            # No need to pass the size, volume download will return -1
            # when the stream finishes
            download_disk_sparse(stream, estimated_size, None, dst,
                                 options.bufsize)
        except libvirt.libvirtError:
            preallocated = True
            write_output('WARN: sparseness is not supported')

    if preallocated:
        vol.download(stream, 0, 0, 0)
        sr = StreamAdapter(stream)
        # No need to pass the size, volume download will return -1
        # when the stream finishes
        download_disk(sr, estimated_size, None, dst, options.bufsize)


def handle_path(con, diskno, src, dst, options):
    write_output('Copying disk %d/%d to %s' % (diskno, len(options.source),
                                               dst))
    vm = con.lookupByName(options.vmname)
    info = vm.blockInfo(src)
    physical = info[2]
    if options.verbose:
        capacity = info[0]
        write_output('>>> disk %d, capacity: %d physical %d' %
                     (diskno, capacity, physical))

    vmAdapter = VMAdapter(vm, src)
    download_disk(vmAdapter, physical, physical, dst, options.bufsize)


def validate_disks(options):
    if not (len(options.source) == len(options.dest) and
            len(options.source) == len(options.storagetype)):
        write_output('>>> source, dest, and storage-type have different'
                     ' lengths')
        sys.exit(1)
    elif not all(st in ("volume", "path") for st in options.storagetype):
        write_output('>>> unsupported storage type. (supported: volume, path)')
        sys.exit(1)
    elif not options.allocation == "sparse" and \
            not options.allocation == "preallocated":
        write_output('>>> unsupported allocation policy. (supported: sparse, '
                     'preallocated)')
        sys.exit(1)


def main(argv=None):
    global _start
    _start = time.monotonic_time()

    options = arguments(argv or sys.argv)
    validate_disks(options)

    con = libvirtconnection.open_connection(options.uri,
                                            options.username,
                                            get_password(options))

    write_output('preparing for copy')
    disks = six.moves.zip(options.source, options.dest, options.storagetype)
    for diskno, (src, dst, fmt) in enumerate(disks, start=1):
        if fmt == 'volume':
            handle_volume(con, diskno, src, dst, options)
        elif fmt == 'path':
            handle_path(con, diskno, src, dst, options)
        diskno = diskno + 1
    write_output('Finishing off')
