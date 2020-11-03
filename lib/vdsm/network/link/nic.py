# Copyright 2017-2020 Red Hat, Inc.
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

import io
import logging

from vdsm.network.link.iface import iface


class ReadSpeedValueError(Exception):
    pass


def speed(nic_name):
    """Return the nic speed if it is a legal value, 0 otherwise."""
    interface = iface(nic_name)
    if interface.is_oper_up():
        try:
            return read_speed_using_sysfs(nic_name)
        except Exception:
            logging.debug('cannot read %s speed', nic_name)
    return 0


def read_speed_using_sysfs(nic_name):
    with io.open('/sys/class/net/%s/speed' % nic_name) as f:
        s = int(f.read())
    # the device may have been disabled/downed after checking
    # so we validate the return value as sysfs may return
    # special values to indicate the device is down/disabled
    if s in (2 ** 16 - 1, 2 ** 32 - 1) or s <= 0:
        raise ReadSpeedValueError(s)
    return s


def duplex(nic_name):
    """
    Return whether a device is connected in full-duplex.
    Return 'unknown' if duplex state is not known
    """
    try:
        with open('/sys/class/net/%s/duplex' % nic_name) as f:
            return f.read().strip()
    except IOError:
        return 'unknown'
