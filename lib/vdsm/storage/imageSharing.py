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

import logging
import subprocess

from vdsm import constants
from vdsm import utils

from vdsm.common import commands
from vdsm.common.units import KiB, MiB

from vdsm.storage import exception as se

log = logging.getLogger("storage.imagesharing")
# Time to wait from finishing writing data to dd, until dd exists,
# Ensure that we don't keep the task active forever if dd cannot
# access the storage.
WAIT_TIMEOUT = 30
# Number of bytes to read from the socket and write
# to dd stdin through the pipe. Based on default socket buffer
# size(~80KB) and default pipe buffer size (64K), this should
# minimize system call overhead without consuming too much
# memory.
BUFFER_SIZE = 64 * KiB


def getLengthFromArgs(methodArgs):
    return methodArgs['length']


def copyToImage(dstImgPath, methodArgs):
    totalSize = getLengthFromArgs(methodArgs)
    fileObj = methodArgs['fileObj']

    # Unlike copyFromImage, we don't use direct I/O when writing because:
    # - Images are small so using host page cache is ok.
    # - Images typically aligned to 512 bytes (tar), may fail on 4k storage.
    cmd = [
        constants.EXT_DD,
        "of=%s" % dstImgPath,
        "bs=%s" % MiB,
        # Ensure that data reach physical storage before returning.
        "conv=fsync",
    ]

    log.info("Copy to image %s", dstImgPath)
    with utils.stopwatch(
            "Copy %s bytes" % totalSize, level=logging.INFO, log=log):
        p = commands.start(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        with commands.terminating(p):
            _copyData(fileObj, p.stdin, totalSize)
            try:
                _, err = p.communicate(timeout=WAIT_TIMEOUT)
            except subprocess.TimeoutExpired:
                log.error("timeout waiting for dd process")
                raise se.StorageException()

            if p.returncode != 0:
                log.error("dd failed rc=%s err=%r", p.returncode, err)
                raise se.MiscFileWriteException()


def copyFromImage(dstImgPath, methodArgs):
    fileObj = methodArgs['fileObj']
    bytes_left = total_size = methodArgs['length']

    # Unlike copyToImage, we must use direct I/O to avoid reading stale data
    # from host page cache, in case OVF disk was modified on another host.
    cmd = [
        constants.EXT_DD,
        "if=%s" % dstImgPath,
        "bs=%s" % MiB,
        "count=%s" % (total_size // MiB + 1),
        "iflag=direct",
    ]

    log.info("Copy from image %s", dstImgPath)
    with utils.stopwatch(
            "Copy %s bytes" % total_size, level=logging.INFO, log=log):
        p = commands.start(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with commands.terminating(p):
            _copyData(p.stdout, fileObj, bytes_left)


def _copyData(inFile, outFile, totalSize):
    bytesToRead = totalSize
    while totalSize > 0:
        toRead = min(BUFFER_SIZE, totalSize)

        try:
            data = inFile.read(toRead)
        except IOError as e:
            error = "error reading file: %s" % e
            log.error(error)
            raise se.MiscFileReadException(error)

        if not data:
            error = "partial data %s from %s" % \
                    (bytesToRead - totalSize, bytesToRead)
            log.error(error)
            raise se.MiscFileReadException(error)

        outFile.write(data)
        # outFile may not be a real file object but a wrapper.
        # To ensure that we don't use more memory as the input buffer size
        # we flush on every write.
        outFile.flush()

        totalSize = totalSize - len(data)
