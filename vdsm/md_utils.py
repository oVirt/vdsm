#
# Copyright 2012 Red Hat, Inc.
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

import os


def _parseMdDeviceMap(lines):
    mdUuidMap = {}
    for l in lines:
        tokens = l.strip().split()
        mdUuidMap[os.path.realpath(tokens[-1])] = tokens[-2]
    return mdUuidMap


def getMdDeviceUuidMap():
    """
    returns all md/uuid map eg.
        {'DEVICE': 'UUID', ...}
    """
    try:
        with open('/dev/md/md-device-map') as f:
            return _parseMdDeviceMap(f.readlines())
    except IOError:
        return {}
