#
# Copyright 2018 Red Hat, Inc.
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

from collections import defaultdict

# These constants correspond to values returned by QEMU-GA in osinfo for
# Windows
_WINDOWS_VARIANT_DESKTOP = 'client'
_WINDOWS_VARIANT_SERVER = 'server'

# List of guest architectures understood by Engine
_ARCH_PPC = 'ppc'
_ARCH_PPCLE = 'ppcle'
_ARCH_PPC64 = 'ppc64'
_ARCH_PPC64LE = 'ppc64le'
_ARCH_X86 = 'x86'
_ARCH_X86_64 = 'x86_64'

_WINDOWS_DESKTOP_VERSIONS = {
    '5.0': 'Win 2000',
    '5.1': 'Win XP',
    '6.0': 'Win Vista',
    '6.1': 'Win 7',
    '6.2': 'Win 8',
    '6.3': 'Win 8.1',
    '10.0': 'Win 10',
}

_WINDOWS_SERVER_VERSIONS = {
    '5.2': 'Win 2003',
    '6.0': 'Win 2008',
    '6.1': 'Win 2008 R2',
    '6.2': 'Win 2012',
    '6.3': 'Win 2012 R2',
    '10.0': 'Win 2016',
}


def translate_arch(arch):
    """
    OVirt Engine recognizes only small set of architectures. We have to map
    possible values to the ones understood by Engine.
    """
    arch_map = {
        # The Engine knows only these architectures
        _ARCH_PPC: _ARCH_PPC,
        _ARCH_PPCLE: _ARCH_PPCLE,
        _ARCH_PPC64: _ARCH_PPC64,
        _ARCH_PPC64LE: _ARCH_PPC64LE,
        _ARCH_X86: _ARCH_X86,
        _ARCH_X86_64: _ARCH_X86_64,
        # Everything else has to be mapped to one of those above
        'i686': _ARCH_X86,
        'i586': _ARCH_X86,
        'i386': _ARCH_X86,
    }
    return arch_map.get(arch, 'unknown')


def translate_fsinfo(filesystem):
    """
    Translate dictionary returned by guest-get-fsinfo info dictionary passed on
    by VDSM.
    """
    # Example on Linux:
    # {
    #   "name": "dm-3",
    #   "total-bytes": 442427793408,
    #   "mountpoint": "/home",
    #   "disk": [ ... ],
    #   "used-bytes": 429409058816,
    #   "type": "ext4"
    # },
    filesystem = defaultdict(str, filesystem)
    return {
        "path": filesystem['mountpoint'],
        "total": str(filesystem['total-bytes']),
        "used": str(filesystem['used-bytes']),
        "fs": filesystem['type'],
    }


def translate_linux_osinfo(os_info):
    """
    Translate dictionary returned by guest-get-osinfo for Linux guest into
    guest info used in VDSM and understood by Engine.
    """
    # Example for Fedora 27:
    # {
    #     "id":"fedora"
    #     "kernel-release":"4.13.9-300.fc27.x86_64"
    #     "kernel-version":"#1 SMP Mon Oct 23 13:41:58 UTC 2017"
    #     "machine":"x86_64"
    #     "name":"Fedora",
    #     "pretty-name":"Fedora 27 (Cloud Edition)"
    #     "variant":"Cloud Edition"
    #     "variant-id":"cloud"
    #     "version":"27 (Cloud Edition)"
    #     "version-id":"27"
    # }

    # Treat missing values as empty strings
    os_info = defaultdict(str, os_info)
    return {
        'guestOs': os_info['kernel-release'],
        'guestOsInfo': {
            'type': 'linux',
            'arch': translate_arch(os_info['machine']),
            'kernel': os_info['kernel-release'],
            'distribution': os_info['name'],
            'version': os_info['version-id'],
            'codename': os_info['variant'],
        }
    }


def translate_windows_osinfo(os_info):
    """
    Translate dictionary returned by guest-get-osinfo for Windows guest into
    guest info used in VDSM and understood by Engine.
    """
    # Example for Windows 10:
    # {
    #     "id":"mswindows",
    #     "kernel-release":"10240",
    #     "kernel-version":"10.0",
    #     "machine":"x86_64",
    #     "name":"Microsoft Windows",
    #     "pretty-name":"Windows 10 Enterprise",
    #     "variant":"client",
    #     "variant-id":"client",
    #     "version-id":"10",
    #     "version":"Microsoft Windows 10"
    # }

    # Treat missing values as empty strings
    os_info = defaultdict(str, os_info)
    name = translate_windows_version(
        os_info['kernel-version'], os_info['variant-id'])
    return {
        'guestOs': name,
        'guestOsInfo': {
            'type': 'windows',
            'arch': translate_arch(os_info['machine']),
            'kernel': '',
            'distribution': '',
            'version': os_info['kernel-version'],
            'codename': name,
        }
    }


def translate_windows_version(version, variant):
    """
    Translate Windows version to version string recognized by oVirt Engine.
    The values are copied from oVirt Guest Agent.
    """
    if variant == _WINDOWS_VARIANT_SERVER:
        return _WINDOWS_SERVER_VERSIONS.get(version, 'unknown')
    else:
        return _WINDOWS_DESKTOP_VERSIONS.get(version, 'unknown')
