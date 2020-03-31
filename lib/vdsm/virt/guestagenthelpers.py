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


def translate_fsinfo(filesystem, idx=None):
    """
    Translate dictionary returned by guest-get-fsinfo info dictionary passed on
    by VDSM. When the info is retrieved using libvirt call the keys are
    slightly different.
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
    if idx is not None:
        # Info comes from libvirt
        prefix = 'fs.{:d}.'.format(idx)
        fstype = '{}fstype'.format(prefix)
    else:
        prefix = ''
        fstype = 'type'
    return {
        "path": filesystem[prefix + 'mountpoint'],
        "total": str(filesystem[prefix + 'total-bytes']),
        "used": str(filesystem[prefix + 'used-bytes']),
        "fs": filesystem[fstype],
    }


def translate_linux_osinfo(os_info):
    """
    Translate dictionary returned by guest-get-osinfo for Linux guest into
    guest info used in VDSM and understood by Engine. When the info is
    retrieved using libvirt call the keys are slightly different.
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
    if 'os.id' in os_info:
        # Info comes from libvirt
        prefix = 'os.'
    else:
        prefix = ''
    return {
        'guestOs': os_info[prefix + 'kernel-release'],
        'guestOsInfo': {
            'type': 'linux',
            'arch': translate_arch(os_info[prefix + 'machine']),
            'kernel': os_info[prefix + 'kernel-release'],
            'distribution': os_info[prefix + 'name'],
            'version': os_info[prefix + 'version-id'],
            'codename': os_info[prefix + 'variant'],
        }
    }


def translate_windows_osinfo(os_info):
    """
    Translate dictionary returned by guest-get-osinfo for Windows guest into
    guest info used in VDSM and understood by Engine. When the info is
    retrieved using libvirt call the keys are slightly different.
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
    if 'os.id' in os_info:
        # Info comes from libvirt
        prefix = 'os.'
    else:
        prefix = ''
    return {
        'guestOs': os_info[prefix + 'pretty-name'],
        'guestOsInfo': {
            'type': 'windows',
            'arch': translate_arch(os_info[prefix + 'machine']),
            'kernel': '',
            'distribution': '',
            'version': os_info[prefix + 'kernel-version'],
            'codename': os_info[prefix + 'pretty-name'],
        }
    }


def translate_pci_device(device):
    device = defaultdict(str, device)
    return {
        'device_id': int(device['address']['data']['device-id']),
        'driver_date': device['driver-date'],
        'driver_name': device['driver-name'],
        'driver_version': device['driver-version'],
        'vendor_id': int(device['address']['data']['vendor-id']),
    }
