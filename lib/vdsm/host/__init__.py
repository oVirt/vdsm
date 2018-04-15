#
# Copyright 2008-2017 Red Hat, Inc.
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
from vdsm.common.commands import execCmd
from vdsm import constants

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
                ret, out, err = execCmd([constants.EXT_DMIDECODE,
                                         "-s",
                                         "system-uuid"],
                                        raw=True,
                                        sudo=True)
                out = '\n'.join(line for line in out.splitlines()
                                if not line.startswith('#'))

                if ret == 0 and 'Not' not in out:
                    # Avoid error string - 'Not Settable' or 'Not Present'
                    host_UUID = out.strip()
                else:
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
