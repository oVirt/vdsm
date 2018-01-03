#
# Copyright 2008-2015 Red Hat, Inc.
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

import libvirt


class NotConnectedError(Exception):
    """
    Raised when trying to talk with a vm that was not started yet or was shut
    down.
    """


class TimeoutError(libvirt.libvirtError):
    pass


class Disconnected(object):

    def __init__(self, vmid):
        self.vmid = vmid

    @property
    def connected(self):
        return False

    def __getattr__(self, name):
        raise NotConnectedError("VM %r was not defined yet or was undefined"
                                % self.vmid)


class Defined(Disconnected):
    # Defined, but not running.

    def __init__(self, vmid, dom):
        """
        :param vmid: VM id
        :type vmid: basestring
        :param dom: libvirt domain accessor created by `VM` class
        :type dom: libvirt.virDomain instance (or its wrapper)
        """
        super(Defined, self).__init__(vmid)
        self._dom = dom

    def state(self, *args, **kwargs):
        return self._dom.state(*args, **kwargs)

    def UUIDString(self):
        return self._dom.UUIDString()

    def metadata(self, *args, **kwargs):
        return self._dom.metadata(*args, **kwargs)

    def setMetadata(self, *args, **kwargs):
        self._dom.setMetadata(*args, **kwargs)

    def undefineFlags(self, flags=0):
        self._dom.undefineFlags(flags)


class Notifying(object):
    # virDomain wrapper that notifies vm when a method raises an exception with
    # get_error_code() = VIR_ERR_OPERATION_TIMEOUT

    def __init__(self, dom, tocb):
        self._dom = dom
        self._cb = tocb

    @property
    def connected(self):
        return True

    def __getattr__(self, name):
        attr = getattr(self._dom, name)
        if not callable(attr):
            return attr

        def f(*args, **kwargs):
            try:
                ret = attr(*args, **kwargs)
                self._cb(False)
                return ret
            except libvirt.libvirtError as e:
                if e.get_error_code() == libvirt.VIR_ERR_OPERATION_TIMEOUT:
                    self._cb(True)
                    toe = TimeoutError(e.get_error_message())
                    toe.err = e.err
                    raise toe
                raise
        return f
