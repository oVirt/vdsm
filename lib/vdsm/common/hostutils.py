# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import dbus


def host_in_shutdown():
    '''
    Return True if the host is shutting down, else False

    Connect to the dbus and list all jobs.
    If there is the "shutdown.target" with the state "start" in
    the job list, the host is shutting down.
    '''
    try:
        system_bus = dbus.SystemBus()
        systemd_bus = system_bus.get_object('org.freedesktop.systemd1',
                                            '/org/freedesktop/systemd1')
    except dbus.DBusException:
        return False
    else:
        jobs = systemd_bus.ListJobs(
            dbus_interface='org.freedesktop.systemd1.Manager')

        for job in jobs:
            if 'shutdown.target' in job and 'start' in job:
                return True

    return False
