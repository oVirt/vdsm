#
# Copyright 2015-2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2 of the License, or
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
"""
This module implements the Domain monitoring, and it is responsible to fire
up the proper libvirt events depending on the Domain state change.
Not all the libvirt events are supported.
The main reasons are lack of immediate need and the fact that not all the
libvirt events make sense for container Domains.
"""

from __future__ import absolute_import
from __future__ import division

import logging

from . import doms

import libvirt


def watchdog(get_vm_uuids):
    """
    Must be run periodically to poll all the active container Domain state.
    Fire lifecycle events if a Domain changes state (e.g. disappears).
    The argument `get_vm_uuids` is any callable that produces a sequence of
    currently active domain, by their UUIDs.
    """
    # set for fast __contains__, the get_vm_uuids() return value
    # should never have duplicate anyway
    found = set(get_vm_uuids())
    for dom in doms.get_all():
        rt_uuid = dom.runtimeUUIDString()
        if rt_uuid in found:
            logging.debug(
                'container %r still running', rt_uuid)
        else:
            logging.warning(
                'container %r no longer running, sending STOP event', rt_uuid)
            dom.events.fire(libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE,
                            dom,
                            libvirt.VIR_DOMAIN_EVENT_STOPPED,
                            libvirt.VIR_DOMAIN_EVENT_STOPPED_SHUTDOWN)


# TODO: poll container stats (use cgview)
