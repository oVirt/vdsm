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
from __future__ import division

import itertools
import json
import os

import six

from vdsm.common import cache
from vdsm.common.config import config
from vdsm.network import cmd


NET_SYSFS = '/sys/class/net/'
PORT_PREFIX = 'dpdk'
OPERSTATE_UP = 'up'

DPDK_DRIVERS = ('vfio-pci', 'igb_uio', 'uio_pci_generic')


class LshwError(Exception):
    pass


def get_dpdk_devices():
    if not config.getboolean('vars', 'dpdk_enable'):
        return {}

    dpdk_devices = _get_dpdk_devices()
    if not _dpdk_devs_current(dpdk_devices):
        invalidate_dpdk_devices()
        dpdk_devices = _get_dpdk_devices()

    return dpdk_devices


def invalidate_dpdk_devices():
    _get_dpdk_devices.invalidate()


def info(dev):
    return {'hwaddr': _get_hw_addr(dev.name), 'pciaddr': dev.pci_addr}


def link_info(dev_name, pci_addr=None):
    """Returns a dictionary with the information of the link object."""
    return {
        'index': '',
        'qdisc': '',
        'name': dev_name,
        'mtu': '',
        'state': 'up',
        'flags': '',
        'address': _get_hw_addr(dev_name),
        'type': 'dpdk',
        'pci_addr': pci_addr,
    }


def pci_addr(dev_name):
    return get_dpdk_devices()[dev_name]['pci_addr']


def is_dpdk(dev_name):
    return dev_name.startswith(PORT_PREFIX) and not os.path.exists(
        os.path.join(NET_SYSFS, dev_name)
    )


def speed(dev_name):
    # todo: return port actual speed
    return 0


def operstate(dev_name):
    # todo: return actual operstate
    return OPERSTATE_UP


def is_oper_up(dev_name):
    return operstate(dev_name) == OPERSTATE_UP


def up(dev_name):
    # todo: set link up
    pass


def down(dev_name):
    # todo: set link down
    pass


@cache.memoized
def _get_dpdk_devices():
    devices = _lshw_command()
    return {
        PORT_PREFIX
        + str(i): {
            'pci_addr': devinfo['handle'][len('PCI:') :],
            'driver': devinfo['configuration']['driver'],
        }
        for i, devinfo in enumerate(_dpdk_devices_info(devices))
    }


def _lshw_command():
    filter_out_hw = [
        'usb',
        'pcmcia',
        'isapnp',
        'ide',
        'scsi',
        'dmi',
        'memory',
        'cpuinfo',
    ]
    filterout_cmd = list(
        itertools.chain.from_iterable(('-disable', x) for x in filter_out_hw)
    )
    rc, out, err = cmd.exec_sync(['lshw', '-json'] + filterout_cmd)
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


def _dpdk_devs_current(dpdk_devices):
    devs_exist = all(
        _dev_exists(devinfo) for devinfo in six.viewvalues(dpdk_devices)
    )
    unlisted_devices = _unlisted_devices(
        [devinfo['pci_addr'] for devinfo in six.viewvalues(dpdk_devices)]
    )

    return devs_exist and not unlisted_devices


def _dev_exists(dev):
    return os.path.exists(
        os.path.join('/sys/bus/pci/drivers', dev['driver'], dev['pci_addr'])
    )


def _unlisted_devices(pci_addrs):
    pci_addr_files = []
    for driver in DPDK_DRIVERS:
        driver_path = os.path.join('/sys/bus/pci/drivers', driver)
        if os.path.exists(driver_path):
            pci_addr_files += [
                pci_addr
                for pci_addr in os.listdir(driver_path)
                if pci_addr.startswith('00')
            ]

    new_devices = set(pci_addr_files) - set(pci_addrs)
    return bool(new_devices)


def _is_dpdk_dev(dev):
    return (
        dev.get('class') == 'network'
        and 'handle' in dev
        and 'logicalname' not in dev
        and dev['configuration'].get('driver') in DPDK_DRIVERS
        and 'Virtual Function' not in dev.get('product', '')
    )


def _normalize_lshw_result(result):
    """
    `lshw -json` does not return a valid JSON in case of several results for
     the same device class, [] brackets are missing in the result.
    """
    return json.loads('[' + result + ']')


def _get_hw_addr(dev_name):
    index = dev_name[len(PORT_PREFIX) :]
    return '02:00:00:00:00:{:02x}'.format(int(index))
