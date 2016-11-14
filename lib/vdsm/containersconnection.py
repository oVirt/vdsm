#
# Copyright 2016 Red Hat, Inc.
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
import threading

import libvirt

try:
    import vdsm.virt.containers.connection
    import vdsm.virt.containers.runner
    from vdsm.virt.containers.docker import available as is_supported
    from vdsm.virt.containers import monitorAllDomains as monitor
    from vdsm.virt.containers import prepare as prepare
    from vdsm.virt.containers import recoveryAllDomains as recovery
    # silence pyflakes
    is_supported
    monitor
    recovery
except ImportError:
    is_supported = lambda: False
    monitor = lambda: None
    prepare = lambda: None
    recovery = lambda: []


_log = logging.getLogger('virt.containers')


class NotAvailable(RuntimeError):
    """
    container connection not available on this host.
    """


def open_connection(uri=None, username=None, passwd=None):
    """
    by calling this method you are getting a new and unwrapped connection
    if you want to use wrapped and cached connection use the get() method
    """
    # no argument is needed, they are present only to mimic libvirtconnection
    if not is_supported():
        raise NotAvailable()
    return vdsm.virt.containers.connection.Connection()


_lock = threading.Lock()
_connections = {}


def get(target=None):
    """Return current connection to libvirt or open a new one.
    Use target to get/create the connection object linked to that object.
    target must have a callable attribute named 'dispatchLibvirtEvents' which
    will be registered as a callback on libvirt events.

    Wrap methods of connection object so that they catch disconnection, and
    take the current process down.
    """
    if not is_supported():
        raise NotAvailable()
    with _lock:
        conn = _connections.get(id(target))
        if conn:
            _log.debug('reusing cached container connection')
            return conn

        _log.debug('trying to connect to container manager')
        conn = open_connection()
        _connections[id(target)] = conn

        if target is not None:
            for event in (libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE,
                          libvirt.VIR_DOMAIN_EVENT_ID_REBOOT,
                          libvirt.VIR_DOMAIN_EVENT_ID_RTC_CHANGE,
                          libvirt.VIR_DOMAIN_EVENT_ID_IO_ERROR_REASON,
                          libvirt.VIR_DOMAIN_EVENT_ID_GRAPHICS,
                          libvirt.VIR_DOMAIN_EVENT_ID_BLOCK_JOB,
                          libvirt.VIR_DOMAIN_EVENT_ID_WATCHDOG):
                conn.domainEventRegisterAny(None,
                                            event,
                                            target.dispatchLibvirtEvents,
                                            event)
        return conn
