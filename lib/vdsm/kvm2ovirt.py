# Copyright 2016-2017 Red Hat, Inc.
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
import itertools
import sys
import threading

from ovirt_imageio_common import directio

from vdsm import libvirtconnection
from vdsm.common import concurrent
from vdsm.common import time
from vdsm.common.password import ProtectedPassword

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

    def finish(self):
        self._stream.finish()


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
                        required=True, help='Storage type (file or block)')
    parser.add_argument('--vm-name', dest='vmname', required=True,
                        help='Libvirt source VM name')
    parser.add_argument('--bufsize', dest='bufsize', default=1048576,
                        type=int, help='Size of packets in bytes, default'
                        '1048676')
    parser.add_argument('--verbose', action='store_true',
                        help='verbose output')
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


def get_password(options):
    if not options.password_file:
        return None
    if options.verbose:
        write_output('>>> Reading password from file %s' %
                     options.password_file)
    with open(options.password_file, 'r') as f:
        return ProtectedPassword(f.read())


def handle_file(con, diskno, src, dst, options):
    write_output('Copying disk %d/%d to %s' % (diskno, len(options.source),
                                               dst))
    vol = con.storageVolLookupByPath(src)
    _, capacity, allocation = vol.info()
    if options.verbose:
        write_output('>>> disk %d, capacity: %d allocation %d' %
                     (diskno, capacity, allocation))

    estimated_size = capacity
    stream = con.newStream()
    vol.download(stream, 0, 0, 0)
    sr = StreamAdapter(stream)
    # No need to pass the size, volume download will return -1
    # when stream finish
    download_disk(sr, estimated_size, None, dst, options.bufsize)


def handle_block(con, diskno, src, dst, options):
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
    elif not all(st in ("file", "block") for st in options.storagetype):
        write_output('>>> unsupported storage type. (supported: file, block)')
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
    disks = itertools.izip(options.source, options.dest, options.storagetype)
    for diskno, (src, dst, fmt) in enumerate(disks, start=1):
        if fmt == 'file':
            handle_file(con, diskno, src, dst, options)
        elif fmt == 'block':
            handle_block(con, diskno, src, dst, options)
        diskno = diskno + 1
    write_output('Finishing off')
