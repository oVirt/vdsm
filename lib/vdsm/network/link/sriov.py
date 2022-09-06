# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager
from glob import glob
import os

from vdsm.network import netconfpersistence
from vdsm.network.netlink import waitfor


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


def list_sriov_pci_devices():
    sysfs_devs_path = glob('/sys/bus/pci/devices/*/sriov_totalvfs')
    return {
        sysfs_dev_path.rsplit('/', 2)[-2] for sysfs_dev_path in sysfs_devs_path
    }


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
