# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
