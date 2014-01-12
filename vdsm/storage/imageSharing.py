# Copyright 2013 Red Hat, Inc.
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

import logging
import signal
import socket

import curlImgWrap
from vdsm import constants
from vdsm import utils
import storage_exception as se


log = logging.getLogger("Storage.ImageSharing")
# Time to wait from finishing writing data to dd, until dd exists,
# Ensure that we don't keep the task active forever if dd cannot
# access the storage.
WAIT_TIMEOUT = 30
# Number of bytes to read from the socket and write
# to dd stdin trough the pipe. Based on default socket buffer
# size(~80KB) and default pipe buffer size (64K), this should
# minimize system call overhead without consuming too much
# memory.
BUFFER_SIZE = 65536


def httpGetSize(methodArgs):
    headers = curlImgWrap.head(methodArgs.get('url'),
                               methodArgs.get("headers", {}))

    size = None

    if 'Content-Length' in headers:
        size = int(headers['Content-Length'])

    # OpenStack Glance returns Content-Length = 0 so we need to
    # override the value with the content of the custom header
    # X-Image-Meta-Size.
    if 'X-Image-Meta-Size' in headers:
        size = max(size, int(headers['X-Image-Meta-Size']))

    if size is None:
        raise RuntimeError("Unable to determine image size")

    return size


def streamGetSize(methodArgs):
    return methodArgs['contentLength']


def httpDownloadImage(dstImgPath, methodArgs):
    curlImgWrap.download(methodArgs.get('url'), dstImgPath,
                         methodArgs.get("headers", {}))


def httpUploadImage(srcImgPath, methodArgs):
    curlImgWrap.upload(methodArgs.get('url'), srcImgPath,
                       methodArgs.get("headers", {}))


def streamDownloadImage(dstImgPath, methodArgs):
    bytes_left = streamGetSize(methodArgs)
    stream = methodArgs['fileObj']

    cmd = [constants.EXT_DD, "of=%s" % dstImgPath, "bs=%s" % constants.MEGAB]
    p = utils.execCmd(cmd, sudo=False, sync=False,
                      deathSignal=signal.SIGKILL)
    try:
        while bytes_left > 0:
            to_read = min(BUFFER_SIZE, bytes_left)

            try:
                data = stream.read(to_read)
            except socket.timeout:
                log.error("socket timeout")
                raise se.MiscFileReadException()

            if not data:
                total_size = streamGetSize(methodArgs)
                log.error("partial data %s from %s",
                          total_size - bytes_left, total_size)
                raise se.MiscFileReadException()

            p.stdin.write(data)
            # Process stdin is not a real file object but a wrapper using
            # StringIO buffer. To ensure that we don't use more memory if we
            # get data faster then dd read it from the pipe, we flush on every
            # write. We can remove flush() we can limit the buffer size used
            # by this stdin wrapper.
            p.stdin.flush()
            bytes_left = bytes_left - len(data)

        p.stdin.close()
        if not p.wait(WAIT_TIMEOUT):
            log.error("timeout waiting for dd process")
            raise se.StorageException()

        if p.returncode != 0:
            log.error("dd error - code %s, stderr %s",
                      p.returncode, p.stderr.read(1000))
            raise se.MiscFileWriteException()

    except Exception:
        if p.returncode is None:
            p.kill()
        raise


_METHOD_IMPLEMENTATIONS = {
    'http': (httpGetSize, httpDownloadImage, httpUploadImage),
}


def _getSharingMethods(methodArgs):
    try:
        method = methodArgs['method']
    except KeyError:
        raise RuntimeError("Sharing method not specified")

    try:
        return _METHOD_IMPLEMENTATIONS[method]
    except KeyError:
        raise RuntimeError("Sharing method %s not found" % method)


def getSize(methodArgs):
    getSizeImpl, _, _ = _getSharingMethods(methodArgs)
    return getSizeImpl(methodArgs)


def download(dstImgPath, methodArgs):
    _, downloadImageImpl, _ = _getSharingMethods(methodArgs)
    downloadImageImpl(dstImgPath, methodArgs)


def upload(srcImgPath, methodArgs):
    _, _, uploadImageImpl = _getSharingMethods(methodArgs)
    uploadImageImpl(srcImgPath, methodArgs)
