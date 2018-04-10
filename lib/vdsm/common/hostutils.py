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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
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
