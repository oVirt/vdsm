# Copyright 2017-2018 Red Hat, Inc.
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

from glob import glob
import logging
import os
import time

import six

from vdsm.common import udevadm
from vdsm.network import netconfpersistence

from .iface import iface


DRIVERS_PATH = '/sys/bus/pci/drivers/'
_SYSFS_SRIOV_NUMVFS = '/sys/bus/pci/devices/{}/sriov_numvfs'


def update_numvfs(pci_path, numvfs):
    """pci_path is a string looking similar to "0000:00:19.0"
    """
    with open(_SYSFS_SRIOV_NUMVFS.format(pci_path), 'w', 0) as f:
        # Zero needs to be written first in order to remove previous VFs.
        # Trying to just write the number (if n > 0 VF's existed before)
        # results in 'write error: Device or resource busy'
        # https://www.kernel.org/doc/Documentation/PCI/pci-iov-howto.txt
        f.write('0')
        f.write(str(numvfs))
        _wait_for_udev_events()
        _set_valid_vf_macs(pci_path, numvfs)


def persist_numvfs(device_name, numvfs):
    running_config = netconfpersistence.RunningConfig()
    running_config.set_device(
        device_name,
        {'sriov': {'numvfs': numvfs}}
    )
    running_config.save()


def _set_valid_vf_macs(pci_path, numvfs):
    """
        some drivers forbid resetting VF MAC address back to 00:00:00:00:00:00,
        which was its original value. By setting the MAC addresses to a valid
        value, upon restoration the valid address will be accepted.

        The drivers and their BZ's:
        1) igb: https://bugzilla.redhat.com/1341248
        2) ixgbe: https://bugzilla.redhat.com/1415609

        Once resolved, this method and its accompanying methods should
        be removed.
    """
    if _is_zeromac_limited_driver(pci_path):
        _modify_mac_addresses(pci_path, numvfs)


def _is_zeromac_limited_driver(pci_path):
    ZEROMAC_LIMITED_DRIVERS = ('igb',
                               'ixgbe',)

    for driver in ZEROMAC_LIMITED_DRIVERS:
        driver_path = DRIVERS_PATH + driver

        if (os.path.exists(driver_path) and
                pci_path in os.listdir(driver_path)):
                    return True

    return False


def _modify_mac_addresses(pci_path, numvfs):
    TARGET_MAC = '02:00:00:00:00:01'

    pf = pciaddr2devname(pci_path)
    for vf_num in range(numvfs):
        iface(pf, vfid=vf_num).set_address(TARGET_MAC)


def _wait_for_udev_events():
    # FIXME: This is an ugly hack that is meant to prevent VDSM to report VFs
    # that are not yet named by udev or not report all of. This is a blocking
    # call that should wait for all udev events to be handled. a proper fix
    # should be registering and listening to the proper netlink and udev
    # events. The sleep prior to observing udev is meant to decrease the
    # chances that we wait for udev before it knows from the kernel about the
    # new devices.
    time.sleep(0.5)
    udevadm.settle(timeout=10)


def list_sriov_pci_devices():
    sysfs_devs_path = glob('/sys/bus/pci/devices/*/sriov_totalvfs')
    return {sysfs_dev_path.rsplit('/', 2)[-2]
            for sysfs_dev_path in sysfs_devs_path}


def upgrade_devices_sriov_config(cfg):
    """
    Given an old SRIOV PF configuration (containing numvfs per device), convert
    it to the new device configuration and return it.
    """
    devices = {}
    for dev_pci_path, numvfs in six.viewitems(cfg):
        try:
            pf_devname = pciaddr2devname(dev_pci_path)
        except OSError:
            logging.error(
                'Device %s does not exist, skipping its config upgrade.' %
                dev_pci_path)
            continue
        devices[pf_devname] = {
            'sriov': {
                'numvfs': numvfs
            }
        }

    return devices


def get_old_persisted_devices_numvfs(devs_vfs_path):
    """
    Reads the persisted SRIOV VFs old configuration and returns a dict where
    the device PCI is the key and the number of VF/s is the value.
    """
    numvfs_by_device = {}

    for file_name in os.listdir(devs_vfs_path):
        with open(os.path.join(devs_vfs_path, file_name)) as f:
            numvfs_by_device[file_name] = int(f.read().strip())

    return numvfs_by_device


def pciaddr2devname(pci_path):
    return os.listdir('/sys/bus/pci/devices/{}/net/'.format(pci_path))[0]


def devname2pciaddr(devname):
    with open('/sys/class/net/{}/device/uevent'.format(devname)) as f:
        data = [line for line in f if line.startswith('PCI_SLOT_NAME')]
        if not data:
            raise DeviceHasNoPciAddress('device: {}'.format(devname))
        return data[0].strip().split('=', 1)[-1]


class DeviceHasNoPciAddress(Exception):
    pass
