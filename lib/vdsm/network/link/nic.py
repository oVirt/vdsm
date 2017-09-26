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

import errno
import io
import logging

from vdsm.network.ethtool import driver_name
from vdsm.network.link import dpdk
from vdsm.network.link.iface import iface


class ReadSpeedValueError(Exception):
    pass


def speed(nic_name):
    """Return the nic speed if it is a legal value, 0 otherwise."""
    interface = iface(nic_name)
    if interface.is_oper_up():
        if dpdk.is_dpdk(nic_name):
            return dpdk.speed(nic_name)
        try:
            return read_speed_using_sysfs(nic_name)
        except IOError as ose:
            if ose.errno == errno.EINVAL:
                return _ib_hacked_speed(nic_name)
            else:
                logging.exception('cannot read %s speed', nic_name)
        except Exception:
            logging.exception('cannot read %s speed', nic_name)
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


def _ib_hacked_speed(nic_name):
    """If the nic is an InfiniBand device, return a speed of 10000 Mbps.

    This is only needed until the kernel reports ib*/speed, see
    https://bugzilla.redhat.com/show_bug.cgi?id=1101314
    """
    try:
        return 10000 if driver_name(nic_name) == 'ib_ipoib' else 0
    except IOError:
        return 0
