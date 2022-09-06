# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import functools
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

    @property
    def dom(self):
        """
        Access the underlying libvirt.virDomain object.

        WARNING: should be used only when the underlying libvirt.virDomain
        object must be passed as an argument to libvirt.
        """
        return self._dom

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
    def dom(self):
        """
        Access the underlying libvirt.virDomain object.

        WARNING: should be used only when the underlying libvirt.virDomain
        object must be passed as an argument to libvirt.
        """
        return self._dom

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


def expose(*method_names):
    """
    Add methods for calling underlying Vm._dom to the decorated class.

    Expected usage is for Vm wrappers, exposing certain libvirt API methods to
    a sub system. An example use case is to allow the backup module to begin
    and end backups, without making Vm._dom public.

    Example usage:

        @virdomain.expose("backupBegin", "backupEnd")
        class BackupDomain(object):
            def __init__(self, vm):
                self._vm = vm

    Note that the decorated class must keep the Vm instance in the _vm
    attribute.

    When we need to pass Vm._dom to the subsystem, we create the backup domain
    instance by wrapping the Vm instance:

        backup_dom = BackupDomain(vm)

    Then we pass backup_dom to the subsystem, which can use it exactly like the
    wrapped Vm._dom object:

        backup_id =  backup_dom.backupBegin(backup_xml, checkopoint_xml)

    """
    def class_decorator(cls):
        for name in method_names:
            setattr(cls, name, _call(name))
        return cls

    return class_decorator


def _call(name):
    """
    Generate a method calling underlying libvirt domain object method.
    """
    orig_meth = getattr(libvirt.virDomain, name)

    @functools.wraps(orig_meth)
    def call(self, *a, **kw):
        return getattr(self._vm._dom, name)(*a, **kw)

    return call
