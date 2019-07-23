#
# Copyright 2008-2019 Red Hat, Inc.
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

import os
import logging

from vdsm.common import cpuarch
from vdsm.common.cache import memoized
from vdsm import dmidecodeUtil

P_VDSM_NODE_ID = '/etc/vdsm/vdsm.id'


@memoized
def uuid():
    host_UUID = None

    try:
        if os.path.exists(P_VDSM_NODE_ID):
            with open(P_VDSM_NODE_ID) as f:
                host_UUID = f.readline().replace("\n", "")
        else:
            arch = cpuarch.real()
            if cpuarch.is_x86(arch):
                try:
                    hw_info = dmidecodeUtil.getHardwareInfoStructure()
                    host_UUID = hw_info['systemUUID'].lower()
                except KeyError:
                    logging.warning('Could not find host UUID.')
            elif cpuarch.is_ppc(arch):
                # eg. output IBM,03061C14A
                try:
                    with open('/proc/device-tree/system-id') as f:
                        systemId = f.readline()
                        host_UUID = systemId.rstrip('\0').replace(',', '')
                except IOError:
                    logging.warning('Could not find host UUID.')

    except:
        logging.error("Error retrieving host UUID", exc_info=True)

    return host_UUID
