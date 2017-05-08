#
# Copyright 2017 Red Hat, Inc.
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

import itertools
import json
import os

from vdsm.commands import execCmd


NET_SYSFS = '/sys/class/net/'
PORT_PREFIX = 'dpdk'

DPDK_DRIVERS = ('vfio-pci', 'igb_uio', 'uio_pci_generic')


class LshwError(Exception):
    pass


def get_dpdk_devices():
    devices = _lshw_command()

    dpdk_devices = {PORT_PREFIX + str(i): devinfo['handle'][len('PCI:'):]
                    for i, devinfo in enumerate(_dpdk_devices_info(devices))}

    return dpdk_devices


def info(dev_name, dev_addr):
    return {
        'hwaddr': _get_hw_addr(dev_name),
        'pciaddr': dev_addr
    }


def is_dpdk(dev_name):
    return (dev_name.startswith(PORT_PREFIX) and
            not os.path.exists(os.path.join(NET_SYSFS, dev_name)))


def _lshw_command():
    filter_out_hw = ['usb', 'pcmcia', 'isapnp', 'ide', 'scsi', 'dmi', 'memory',
                     'cpuinfo']
    filterout_cmd = list(itertools.chain.from_iterable(('-disable', x)
                                                       for x in filter_out_hw))
    rc, out, err = execCmd(['lshw', '-json'] + filterout_cmd, raw=True)
    if rc != 0:
        raise LshwError(err)

    return _normalize_lshw_result(out)


def _dpdk_devices_info(data):
    if isinstance(data, list):
        for devices in data:
            for devinfo in _dpdk_devices_info(devices):
                yield devinfo
    else:
        for entry in data.get('children', []):
            if _is_dpdk_dev(entry):
                yield entry

            for child in _dpdk_devices_info(entry):
                yield child


def _is_dpdk_dev(dev):
    return (dev.get('class') == 'network' and 'handle' in dev and
            'logicalname' not in dev and
            dev['configuration']['driver'] in DPDK_DRIVERS)


def _normalize_lshw_result(result):
    """
    `lshw -json` does not return a valid JSON in case of several results for
     the same device class, [] brackets are missing in the result.
    """
    return json.loads((b'[' + result + b']').decode('utf-8'))


def _get_hw_addr(dev_name):
    index = dev_name[len(PORT_PREFIX):]
    return '02:00:00:00:00:{:02x}'.format(int(index))
