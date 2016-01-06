#
# Copyright 2008-2016 Red Hat, Inc.
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
import os
import logging
from .commands import execCmd
from . import constants
from . import cpuarch

__hostUUID = None


def uuid(legacy=False):
    global __hostUUID

    if legacy:
        raise NotImplementedError

    if __hostUUID:
        return __hostUUID

    __hostUUID = None

    try:
        if os.path.exists(constants.P_VDSM_NODE_ID):
            with open(constants.P_VDSM_NODE_ID) as f:
                __hostUUID = f.readline().replace("\n", "")
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
                    __hostUUID = out.strip()
                else:
                    logging.warning('Could not find host UUID.')
            elif cpuarch.is_ppc(arch):
                # eg. output IBM,03061C14A
                try:
                    with open('/proc/device-tree/system-id') as f:
                        systemId = f.readline()
                        __hostUUID = systemId.rstrip('\0').replace(',', '')
                except IOError:
                    logging.warning('Could not find host UUID.')

    except:
        logging.error("Error retrieving host UUID", exc_info=True)

    return __hostUUID
