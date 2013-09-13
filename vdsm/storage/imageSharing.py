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

import curlImgWrap


log = logging.getLogger("Storage.ImageSharing")


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


def httpDownloadImage(dstImgPath, methodArgs):
    curlImgWrap.download(methodArgs.get('url'), dstImgPath,
                         methodArgs.get("headers", {}))


def httpUploadImage(srcImgPath, methodArgs):
    curlImgWrap.upload(methodArgs.get('url'), srcImgPath,
                       methodArgs.get("headers", {}))


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
