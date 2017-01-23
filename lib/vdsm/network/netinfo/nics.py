#
# Copyright 2015 Hat, Inc.
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
from __future__ import absolute_import
import errno
import io
from functools import partial
import logging

from vdsm.network.ipwrapper import drv_name, Link
from .misc import visible_devs

OPERSTATE_UP = 'up'
OPERSTATE_UNKNOWN = 'unknown'
OPERSTATE_DOWN = 'down'


nics = partial(visible_devs, Link.isNICLike)


def operstate(nic_name):
    with io.open('/sys/class/net/%s/operstate' % nic_name) as operstateFile:
        return operstateFile.read().strip()


def speed(nic_name):
    """Returns the nic speed if it is a legal value, nicName refers to a
    nic and nic is UP, 0 otherwise."""
    try:
        if operstate(nic_name) == OPERSTATE_UP:
            with io.open('/sys/class/net/%s/speed' % nic_name) as speedFile:
                s = int(speedFile.read())
            # the device may have been disabled/downed after checking
            # so we validate the return value as sysfs may return
            # special values to indicate the device is down/disabled
            if s not in (2 ** 16 - 1, 2 ** 32 - 1) and s > 0:
                return s
    except IOError as ose:
        if ose.errno == errno.EINVAL:
            return _ibHackedSpeed(nic_name)
        else:
            logging.exception('cannot read %s nic speed', nic_name)
    except Exception:
        logging.exception('cannot read %s speed', nic_name)
    return 0


def _ibHackedSpeed(nic_name):
    """If the nic is an InfiniBand device, return a speed of 10000 Mbps.

    This is only needed until the kernel reports ib*/speed, see
    https://bugzilla.redhat.com/show_bug.cgi?id=1101314
    """
    try:
        return 10000 if drv_name(nic_name) == 'ib_ipoib' else 0
    except IOError:
        return 0


def info(link):
    return {'hwaddr': link.address, 'speed': speed(link.name)}
