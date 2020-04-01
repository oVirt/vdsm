# Copyright 2017-2020 Red Hat, Inc.
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

from contextlib import contextmanager
from glob import glob
import logging
import os

import six

from vdsm.network import netconfpersistence
from vdsm.network.netlink import waitfor

from .iface import iface


DRIVERS_PATH = '/sys/bus/pci/drivers/'
_SYSFS_SRIOV_NUMVFS = '/sys/bus/pci/devices/{}/sriov_numvfs'
ZERO_PCI_ADDRESS = '0000:00:00.0'


def update_numvfs(pci_path, numvfs):
    """pci_path is a string looking similar to "0000:00:19.0"
    """
    with open(_SYSFS_SRIOV_NUMVFS.format(pci_path), 'wb', 0) as f:
        # Zero needs to be written first in order to remove previous VFs.
        # Trying to just write the number (if n > 0 VF's existed before)
        # results in 'write error: Device or resource busy'
        # https://www.kernel.org/doc/Documentation/PCI/pci-iov-howto.txt
        links = get_all_vf_names(pci_path)
        if links:
            with waitfor.wait_for_link_event(
                '*',
                waitfor.DELLINK_STATE_DOWN,
                timeout=60,
                check_event=lambda event: _check_all_vfs_down(event, links),
            ):
                f.write(b'0')

        if int(numvfs) > 0:
            vfs_up = []
            with waitfor.wait_for_link_event(
                '*',
                waitfor.NEWLINK_STATE_UP,
                timeout=60,
                check_event=lambda event: _check_all_vfs_up(
                    event, vfs_up, int(numvfs), pci_path
                ),
            ):
                f.write(b'%d' % numvfs)
            _set_valid_vf_macs(pci_path, numvfs)


def persist_numvfs(device_name, numvfs):
    running_config = netconfpersistence.RunningConfig()
    running_config.set_device(device_name, {'sriov': {'numvfs': numvfs}})
    running_config.save()


@contextmanager
def wait_for_pci_link_up(pci_path, timeout=60):
    with waitfor.wait_for_link_event(
        '*',
        waitfor.NEWLINK_STATE_UP,
        timeout=timeout,
        check_event=lambda event: _is_event_from_pci_path(event, pci_path),
    ):
        yield


def _is_event_from_pci_path(event, pci_path):
    dev_name = event.get('name')
    return pci_path == devname2pciaddr(dev_name)


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
    ZEROMAC_LIMITED_DRIVERS = ('igb', 'ixgbe')

    for driver in ZEROMAC_LIMITED_DRIVERS:
        driver_path = DRIVERS_PATH + driver

        if os.path.exists(driver_path) and pci_path in os.listdir(driver_path):
            return True

    return False


def _modify_mac_addresses(pci_path, numvfs):
    TARGET_MAC = '02:00:00:00:00:01'

    pf = pciaddr2devname(pci_path)
    for vf_num in range(numvfs):
        iface(pf, vfid=vf_num).set_address(TARGET_MAC)


def _check_all_vfs_up(event, vfs_up, numvfs, parent_pci_address):
    dev_name = event.get('name')
    if physical_function_to_pci_address(dev_name) == parent_pci_address:
        logging.info("New VF link up: %s", dev_name)
        vfs_up.append(dev_name)

    return len(vfs_up) == numvfs


def physical_function_to_pci_address(devname):
    try:
        with open(
            '/sys/class/net/{}/device/physfn/uevent'.format(devname)
        ) as f:
            data = [line for line in f if line.startswith('PCI_SLOT_NAME')]
            if not data:
                return None
            return data[0].strip().split('=', 1)[-1]
    except IOError:
        return ZERO_PCI_ADDRESS


def _check_all_vfs_down(event, links):
    dev_name = event.get('name')
    if dev_name in links:
        logging.info("VF link removed: %s", dev_name)
        links.remove(dev_name)

    return not links


def get_all_vf_names(pci_addr):
    links = []
    for dirs in glob('/sys/bus/pci/devices/{}/virtfn*/net/'.format(pci_addr)):
        ifname = os.listdir(dirs)
        if ifname:
            links.append(ifname[0])
    return links


def list_sriov_pci_devices():
    sysfs_devs_path = glob('/sys/bus/pci/devices/*/sriov_totalvfs')
    return {
        sysfs_dev_path.rsplit('/', 2)[-2] for sysfs_dev_path in sysfs_devs_path
    }


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
                'Device %s does not exist, skipping its config upgrade.'
                % dev_pci_path
            )
            continue
        devices[pf_devname] = {'sriov': {'numvfs': numvfs}}

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
