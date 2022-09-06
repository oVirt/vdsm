# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import re

from collections import namedtuple

from vdsm.common import cache
from vdsm.common import cpuarch


_PATH = '/proc/cpuinfo'
CpuInfo = namedtuple('CpuInfo', 'flags, frequency, model, ppcmodel, platform,'
                     'machine')


@cache.memoized
def _cpuinfo():
    '''
    Parse cpuinfo-like file, keeping the values in module's runtime variables.

    Arguments:

    source      Optional. Accepts a string indicating path to the cpuinfo-like
                file. If not supplied, default path (/proc/cpuinfo) is used.
    '''
    fields = {}

    if cpuarch.is_ppc(cpuarch.real()):
        fields['flags'] = ['powernv']
    if cpuarch.is_x86(cpuarch.real()):
        fields['platform'] = 'unavailable'
        fields['machine'] = 'unavailable'
        fields['ppcmodel'] = 'unavailable'
    if cpuarch.is_arm(cpuarch.real()):
        fields['platform'] = 'unavailable'
        fields['machine'] = 'unavailable'
        fields['ppcmodel'] = 'unavailable'
    if cpuarch.is_s390(cpuarch.real()):
        fields['platform'] = 'unavailable'
        fields['machine'] = 'unavailable'
        fields['ppcmodel'] = 'unavailable'

    with open(_PATH) as info:
        for line in info:
            if not line.strip():
                continue

            key, value = [part.strip() for part in line.split(':', 1)]

            if key == 'flags':  # x86_64
                fields['flags'] = value.split()
            elif key == 'Features':  # aarch64
                fields['flags'] = value.split()
            elif key == 'features':  # s390
                fields['flags'] = value.split()
            elif key == 'cpu MHz':  # x86_64
                fields['frequency'] = value
            elif key == 'BogoMIPS':  # aarch64
                fields['frequency'] = value
            elif key == 'clock':  # ppc64, ppc64le
                fields['frequency'] = value[:-3]
            elif key == 'cpu MHz dynamic':  # s390
                # s390 reports both static and dynamic frequencies with
                # dynamic <= stat (nominal), so dynamic matches the
                # x86_64 frequency semantics.
                fields['frequency'] = value
            elif key == 'model name':  # x86_64
                fields['model'] = value
            elif key == 'CPU part':  # aarch64
                fields['model'] = value
            elif re.match(r'processor \d+', key):  # s390
                match = re.search(r'\bmachine\s*=\s*(\w+)', value)
                if match:
                    fields['model'] = match.group(1)
            elif key == 'model':  # ppc64le
                fields['ppcmodel'] = value
            elif key == 'cpu':  # ppc64, ppc64le
                fields['model'] = value
            elif key == 'platform':  # ppc64, ppc64le
                fields['platform'] = value
            elif key == 'machine':  # ppc64, ppc64le
                fields['machine'] = value

            if len(fields) == 6:
                break

        # Older s390 machine versions don't report frequency.
        if 'frequency' not in fields:
            fields['frequency'] = 'unavailable'

        return CpuInfo(**fields)


def flags():
    '''
    Get the CPU flags.

    Returns:

    A list of flags supported by current CPU as parsed by parse() procedure
    or
    raises UnsupportedArchitecture exception or KeyError if cpuinfo format
    is invalid.

    '''
    return _cpuinfo().flags


def frequency():
    '''
    Get the CPU frequency.

    Returns:

    A floating point number representing the CPU frequency in MHz
    or
    raises UnsupportedArchitecture exception or KeyError if cpuinfo format
    is invalid.
    '''
    return _cpuinfo().frequency


def model():
    '''
    Get the CPU identification.

    Returns:

    A string representing the name of the CPU
    or
    raises UnsupportedArchitecture exception or KeyError if cpuinfo format
    is invalid.
    '''
    return _cpuinfo().model


def ppcmodel():
    '''
    Get the POWER CPU identification.

    Returns:

    A string representing the identification of the POWER CPU
    or
    raises UnsupportedArchitecture exception or KeyError if cpuinfo format
    is invalid.
    '''
    return _cpuinfo().ppcmodel


def platform():
    '''
    Get the CPU platform.

    Returns:

    A string representing the platform of POWER CPU
    or
    raises UnsupportedArchitecture exception or KeyError if cpuinfo format
    is invalid.
    '''
    return _cpuinfo().platform


def machine():
    '''
    Get the CPU machine.

    Returns:

    A string representing the name of POWER machine
    or
    raises UnsupportedArchitecture exception or KeyError if cpuinfo format
    is invalid.
    '''
    return _cpuinfo().machine
