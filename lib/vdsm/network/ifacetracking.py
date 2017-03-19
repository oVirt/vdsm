# Copyright 2016-2017 Red Hat, Inc.
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
import os

from vdsm.constants import P_VDSM_RUN
from vdsm.utils import rmFile, touchFile


TRACKED_INTERFACES_FOLDER = os.path.join(P_VDSM_RUN, 'trackedInterfaces')


def add(device_name):
    logging.debug('Add iface tracking for device %s', device_name)
    touchFile(_filepath(device_name))


def remove(device_name):
    logging.debug('Remove iface tracking for device %s', device_name)
    rmFile(_filepath(device_name))


def is_tracked(device_name):
    return bool(os.path.exists(_filepath(device_name)))


def _filepath(device_name):
    return os.path.join(TRACKED_INTERFACES_FOLDER, device_name)
