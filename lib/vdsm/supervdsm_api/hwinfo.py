# Copyright 2016-2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import logging
import os

from vdsm.common import cpuarch
from . import expose


CPU_VULNERABILITY_DIR = '/sys/devices/system/cpu/vulnerabilities'


@expose
def getHardwareInfo(*args, **kwargs):
    arch = cpuarch.real()
    if cpuarch.is_x86(arch):
        from vdsm.dmidecodeUtil import getHardwareInfoStructure
        return getHardwareInfoStructure()
    elif cpuarch.is_ppc(arch):
        from vdsm.ppc64HardwareInfo import getHardwareInfoStructure
        return getHardwareInfoStructure()
    else:
        #  not implemented over other architecture
        return {}


def read_fs_property(path):
    with open(path) as f:
        return f.read().strip()


def read_string_property(path):
    try:
        result = read_fs_property(path)
    except Exception:
        logging.exception("Could not read file: %s", path)
        return -1
    return '(%s)' % (result,)


@expose
def get_cpu_vulnerabilities():
    vulnerabilities = {}
    for file_name in os.listdir(CPU_VULNERABILITY_DIR):
        path = os.path.join(CPU_VULNERABILITY_DIR, file_name)
        if os.path.isfile(path):
            vulnerabilities[file_name.upper()] = read_string_property(path)
    return vulnerabilities
