# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
