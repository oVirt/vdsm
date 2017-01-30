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

import errno
import os
import time

from vdsm import udevadm
from vdsm.network import netconfpersistence

from . import iface


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
        _set_valid_macs_for_igb_vfs(pci_path, numvfs)


def persist_numvfs(device_name, numvfs):
    dir_path = os.path.join(netconfpersistence.CONF_RUN_DIR,
                            'virtual_functions')
    try:
        os.makedirs(dir_path)
    except OSError as ose:
        if errno.EEXIST != ose.errno:
            raise
    with open(os.path.join(dir_path, device_name), 'w') as f:
        f.write(str(numvfs))


def _set_valid_macs_for_igb_vfs(pci_path, numvfs):
    """
        igb driver forbids resetting VF MAC address back to 00:00:00:00:00:00,
        which was its original value. By setting the MAC addresses to a valid
        value (e.g. TARGET_MAC) upon restoration the valid address will
        be accepted.
       (BZ: https://bugzilla.redhat.com/1341248)
    """
    if _is_igb_driver(pci_path):
        _modify_mac_addresses(pci_path, numvfs)


def _is_igb_driver(pci_path):
    igb_driver_path = '/sys/bus/pci/drivers/igb'
    return (os.path.exists(igb_driver_path) and
            pci_path in os.listdir(igb_driver_path))


def _modify_mac_addresses(pci_path, numvfs):
    TARGET_MAC = '02:00:00:00:00:01'

    pf = os.listdir('/sys/bus/pci/devices/{}/net/'.format(pci_path))[0]
    for vf_num in range(numvfs):
        iface.set_mac_address(pf, TARGET_MAC, vf_num=vf_num)


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
