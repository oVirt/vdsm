# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.common import validate
from vdsm.common import xmlutils
from vdsm.common.hostdev import detach_detachable, reattach_detachable, \
    device_name_from_address, spawn_mdev, despawn_mdev, MdevPlacement, \
    MdevProperties
from vdsm.virt import vmxml

from . import core


def _get_device_name_type(dev):
    dev_type = core.find_device_type(dev)
    src_dev = vmxml.find_first(dev, 'source')
    src_addr = vmxml.device_address(src_dev)
    if dev_type == 'scsi':
        src_addr = _normalize_scsi_address(dev, src_addr)
    elif dev_type == 'pci':
        src_addr = _normalize_pci_address(**src_addr)
    elif dev_type == 'mdev':
        return src_addr['uuid'], dev_type
    return device_name_from_address(dev_type, src_addr), dev_type


def _normalize_pci_address(domain, bus, slot, function, **kwargs):
    """
    Wrapper around normalize_pci_address to handle transparently
    the extra fields of the address (e.g. type) which don't need
    normalization.
    """
    kwargs.update(
        **validate.normalize_pci_address(domain, bus, slot, function)
    )
    return kwargs


def _normalize_scsi_address(dev, addr):
    adapter = vmxml.find_attr(dev, 'adapter', 'name')
    addr['host'] = adapter.replace('scsi_host', '')
    addr['lun'] = addr.pop('unit')
    return addr


def _mdev_properties(dev, meta):
    mdev_type = None
    mdev_placement = MdevPlacement.COMPACT
    mdev_driver_parameters = meta.get('mdevDriverParameters')
    mdev_metadata = meta.get('mdevType')
    if mdev_metadata:
        mdev_info = mdev_metadata.split('|')
        mdev_type = mdev_info[0]
        if len(mdev_info) > 1:
            mdev_placement = mdev_info[1]
    return MdevProperties(mdev_type, mdev_placement, mdev_driver_parameters)


def setup_device(dom, meta, log):
    name, type_ = _get_device_name_type(dom)
    if name is None:
        log.debug("Unknown kind of host device: %s",
                  xmlutils.tostring(dom, pretty=True))
    elif type_ == 'mdev':
        spawn_mdev(_mdev_properties(dom, meta), name, log)
    else:
        log.info('Detaching device %s from the host.' % (name,))
        detach_detachable(name)
        log.info('Device %s dettached from the host.' % (name,))


def teardown_device(dom, log):
    name, type_ = _get_device_name_type(dom)
    if name is None:
        log.debug("Unknown kind of host device: %s",
                  xmlutils.tostring(dom, pretty=True))
    elif type_ == 'mdev':
        despawn_mdev(name)
    else:
        pci_reattach = type_ == 'pci'
        log.info('Reattaching device %s to the host.' % (name,))
        reattach_detachable(name, pci_reattach=pci_reattach)
        log.info('Device %s reattached to the host.' % (name,))


class HostDevice(core.HotpluggedDevice):

    @classmethod
    def get_identifying_attrs(cls, dom):
        dev_type = core.find_device_type(dom)
        attrs = {'devtype': dom.tag}
        if dev_type == 'mdev':
            attrs['uuid'] = vmxml.device_address(dom)['uuid']
        else:
            attrs['name'] = core.find_device_alias(dom)
        return attrs

    def setup(self):
        setup_device(self._dom, self._meta, self._log)

    def teardown(self):
        teardown_device(self._dom, self._log)
