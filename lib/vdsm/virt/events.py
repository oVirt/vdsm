#
# Copyright 2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import libvirt

LIBVIRT_EVENTS = {
    libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE: 'LIFECYCLE',
    libvirt.VIR_DOMAIN_EVENT_ID_REBOOT: 'REBOOT',
    libvirt.VIR_DOMAIN_EVENT_ID_RTC_CHANGE: 'RTC_CHANGE',
    libvirt.VIR_DOMAIN_EVENT_ID_IO_ERROR_REASON: 'IO_ERROR_REASON',
    libvirt.VIR_DOMAIN_EVENT_ID_BLOCK_JOB_2: 'BLOCK_JOB',
    libvirt.VIR_DOMAIN_EVENT_ID_GRAPHICS: 'GRAPHICS',
    libvirt.VIR_DOMAIN_EVENT_GRAPHICS_INITIALIZE: 'GRAPHICS_INITIALIZE',
    libvirt.VIR_DOMAIN_EVENT_GRAPHICS_DISCONNECT: 'GRAPHICS_DISCONNECT',
    libvirt.VIR_DOMAIN_EVENT_ID_WATCHDOG: 'WATCHDOG',
    libvirt.VIR_DOMAIN_EVENT_ID_JOB_COMPLETED: 'JOB_COMPLETED'
}


def event_name(event_id):
    try:
        return LIBVIRT_EVENTS[event_id]
    except KeyError:
        return "Unknown id {!r}".format(event_id)
