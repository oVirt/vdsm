# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import ctypes
import io
import logging
import os

from contextlib import closing
from contextlib import contextmanager

from vdsm.common.units import KiB

log = logging.getLogger('storage.directio')

libc = ctypes.CDLL("libc.so.6", use_errno=True)
CharPointer = ctypes.POINTER(ctypes.c_char)

_PC_REC_XFER_ALIGN = 17
_PC_REC_MIN_XFER_SIZE = 16


def open(path, mode="r"):
    return DirectFile(path, mode)


class DirectFile(object):

    def __init__(self, path, mode):
        self._writable = True
        flags = os.O_DIRECT

        if "r" in mode:
            if "+" in mode:
                flags |= os.O_RDWR
            else:
                flags |= os.O_RDONLY
                self._writable = False
        elif "w" in mode:
            flags |= os.O_CREAT | os.O_TRUNC
            if "+" in mode:
                flags |= os.O_RDWR
            else:
                flags |= os.O_WRONLY

        elif "a" in mode:
            flags |= os.O_APPEND
        else:
            raise ValueError("Invalid mode parameter")

        self._mode = mode
        self._fd = os.open(path, flags)
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def fileno(self):
        return self._fd

    @property
    def closed(self):
        return self._closed

    @property
    def mode(self):
        return self._mode

    def seekable(self):
        return True

    def readable(self):
        return True

    def writable(self):
        return self._writable

    def readlines(self):
        return self.readall().splitlines(True)  # Keep ends

    def tell(self):
        return self.seek(0, os.SEEK_CUR)

    @contextmanager
    def _createAlignedBuffer(self, size):
        pbuff = ctypes.c_char_p(0)
        ppbuff = ctypes.pointer(pbuff)
        # Because we usually have fixed sizes for our reads, caching
        # buffers might give a slight performance boost.
        alignment = libc.fpathconf(self.fileno(), _PC_REC_XFER_ALIGN)
        minXferSize = libc.fpathconf(self.fileno(), _PC_REC_MIN_XFER_SIZE)
        chunks, remainder = divmod(size, minXferSize)
        if remainder > 0:
            chunks += 1

        size = chunks * minXferSize

        rc = libc.posix_memalign(ppbuff, alignment, size)
        if rc:
            raise OSError(rc, "Could not allocate aligned buffer")
        try:
            ctypes.memset(pbuff, 0, size)
            yield pbuff
        finally:
            libc.free(pbuff)

    def read(self, n=-1):
        if n < 0:
            return self.readall()

        # TODO: This code is wrong, we should use block size here,
        #  which should be set in __init__.
        #  This is part of 4k support for block storage which is not done yet.
        if n % 512:
            raise ValueError("You can only read in 512 multiples")

        with self._createAlignedBuffer(n) as pbuff:
            numRead = libc.read(self._fd, pbuff, n)
            if numRead < 0:
                err = ctypes.get_errno()
                if err != 0:
                    msg = os.strerror(err)
                    raise OSError(err, msg)
            ptr = CharPointer.from_buffer(pbuff)
            return ptr[:numRead]

    def readall(self):
        # TODO: This is pretty bad buffer size, needs to be improved.
        buffsize = KiB
        res = io.BytesIO()
        with closing(res):
            while True:
                buff = self.read(buffsize)
                res.write(buff)
                if len(buff) < buffsize:
                    return res.getvalue()

    def write(self, data):
        length = len(data)
        # TODO: This code is wrong, we should use block size here,
        #  which should be set in __init__.
        #  This is part of 4k support for block storage which is not done yet.
        if length % 512:
            raise ValueError("You can only write in 512 multiples")
        pdata = ctypes.c_char_p(data)
        with self._createAlignedBuffer(length) as pbuff:
            ctypes.memmove(pbuff, pdata, len(data))
            numWritten = libc.write(self._fd, pbuff, length)
            if numWritten < 0:
                err = ctypes.get_errno()
                if err != 0:
                    msg = os.strerror(err)
                    raise OSError(err, msg)

    def seek(self, offset, whence=os.SEEK_SET):
        return os.lseek(self._fd, offset, whence)

    def close(self):
        if self.closed:
            return

        os.close(self._fd)
        self._closed = True

    def __del__(self):
        if not hasattr(self, "_fd"):
            return

        if not self.closed:
            self.close()
