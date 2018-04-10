#
# Copyright 2015 Red Hat, Inc.
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
from __future__ import division
import ctypes
import logging
import threading


NAME_MAX_LENGTH = 15


_LIBPTHREAD = ctypes.CDLL("libpthread.so.0", use_errno=True)

_pthread_setname_np_proto = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_ulong, ctypes.c_char_p)
_pthread_getname_np_proto = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_size_t)

try:
    _pthread_setname_np = _pthread_setname_np_proto(('pthread_setname_np',
                                                    _LIBPTHREAD))

    _pthread_getname_np = _pthread_getname_np_proto(('pthread_getname_np',
                                                    _LIBPTHREAD))
except AttributeError:
    def _pthread_setname_np(ident, name):
        pass

    def _pthread_getname_np(ident):
        return ""

    logging.warning(
        'pthread_{set,get}name_np unavailable. '
        'System thread names will not be set.')


def setname(name):
    """
    Set a system-wide thread name.

    The most common use of this function is inside a thread target function:

        def run():
            pthread.setname("vdsm-cleanup")
            ...

        Thread(target=run).start()

    The name is limited to 15 ASCII characters - see pthread_setname_np(3).
    """

    name = name.encode("ascii")
    if len(name) > NAME_MAX_LENGTH:
        raise ValueError("Expecting up to %d bytes for the name" % (
            NAME_MAX_LENGTH))

    thread = threading.current_thread()
    _pthread_setname_np(thread.ident, name)


def getname():
    """
    Get the system-wide name of the current thread.

    Return empty string if the thread has no system name.
    """
    bufsize = NAME_MAX_LENGTH + 1
    buf = ctypes.create_string_buffer(b'\0' * bufsize)

    thread = threading.current_thread()
    _pthread_getname_np(thread.ident, buf, bufsize)
    return buf.value.decode('ascii')
