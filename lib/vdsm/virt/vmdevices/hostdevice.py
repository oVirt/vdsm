#
# Copyright 2015-2017 Red Hat, Inc.
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
# pylint: disable=no-member

from __future__ import absolute_import
from __future__ import division

import libvirt

from vdsm.common import conv
from vdsm.common import validate
from vdsm.common.hostdev import get_device_params, detach_detachable, \
    pci_address_to_name, scsi_address_to_adapter, reattach_detachable, \
    device_name_from_address, spawn_mdev, despawn_mdev, MdevPlacement
from vdsm.virt import libvirtxml
from vdsm.virt import vmxml

from . import core
from . import hwclass


class PciDevice(core.Base):
    __slots__ = ('address', 'hostAddress', 'bootOrder', '_deviceParams',
                 'name', 'numa_node', 'driver')

    def __init__(self, log, **kwargs):
        super(PciDevice, self).__init__(log, **kwargs)

        self._deviceParams = get_device_params(self.device)
        self.hostAddress = self._deviceParams.get('address')
        self.numa_node = self._deviceParams.get('numa_node', None)
        self.name = self.device
        self.is_hostdevice = True

    def setup(self):
        self.log.info('Detaching device %s from the host.' % self.device)
        detach_detachable(self.device)

    def teardown(self):
        reattach_detachable(self.device, pci_reattach=False)

    @property
    def _xpath(self):
        """
        Returns xpath to the device in libvirt dom xml.
        The path is relative to the root element.
        """
        address_fields = []
        for key, value in self.hostAddress.items():
            padding, base = '02', '0x'
            if key == 'domain':
                base = '0x'
                padding = '04'
            elif key == 'function':
                base = '0x'
                padding = ''

            address_fields.append('[@{key}="{base}{value:{padding}}"]'.format(
                key=key, value=int(value), base=base, padding=padding))

        return './devices/hostdev/source/address{}'.format(
            ''.join(address_fields))

    def getXML(self):
        """
        Create domxml for a host device.

        <devices>
            <hostdev mode='subsystem' type='pci' managed='no'>
            <source>
                <address domain='0x0000' bus='0x06' slot='0x02'
                function='0x0'/>
            </source>
            <boot order='1'/>
            </hostdev>
        </devices>
        """

        if conv.tobool(self.specParams.get('iommuPlaceholder', False)):
            raise core.SkipDevice

        hostdev = self.createXmlElem(hwclass.HOSTDEV, None)
        hostdev.setAttrs(managed='no', mode='subsystem', type='pci')
        source = hostdev.appendChildWithArgs('source')

        source.appendChildWithArgs(
            'address',
            **_normalize_pci_address(**self.hostAddress)
        )

        if hasattr(self, 'bootOrder'):
            hostdev.appendChildWithArgs('boot', order=self.bootOrder)

        if hasattr(self, 'address'):
            hostdev.appendChildWithArgs(
                'address',
                **_normalize_pci_address(**self.address)
            )

        return hostdev

    @classmethod
    def update_from_xml(cls, vm, device_conf, device_xml):
        alias = core.find_device_alias(device_xml)
        address = vmxml.device_address(device_xml)
        source = vmxml.find_first(device_xml, 'source')
        device = pci_address_to_name(**vmxml.device_address(source))

        # We can assume the device name to be correct since we're
        # inspecting source element. For the address, we may look at
        # both addresses and determine the correct one.
        if pci_address_to_name(**address) == device:
            address = vmxml.device_address(device_xml, 1)

        known_device = False
        for dev in device_conf:
            if dev.device == device:
                dev.alias = alias
                dev.address = address
                known_device = True

        for dev in vm.conf['devices']:
            if dev['device'] == device:
                dev['alias'] = alias
                dev['address'] = address

        if not known_device:
            device = pci_address_to_name(**vmxml.device_address(source))

            hostdevice = {
                'type': hwclass.HOSTDEV,
                'device': device,
                'alias': alias,
                'address': address,
            }
            vm.conf['devices'].append(hostdevice)


class UsbDevice(core.Base):
    __slots__ = ('address', 'hostAddress', 'bootOrder', '_deviceParams',
                 'name', 'numa_node')

    def __init__(self, log, **kwargs):
        super(UsbDevice, self).__init__(log, **kwargs)

        device_params = get_device_params(self.device)
        self.hostAddress = device_params.get('address')
        self.numa_node = None
        self.name = self.device
        self.is_hostdevice = True

    def setup(self):
        detach_detachable(self.device)

    def teardown(self):
        reattach_detachable(self.device)

    @property
    def _xpath(self):
        """
        Returns xpath to the device in libvirt dom xml.
        The path is relative to the root element.
        """
        address_fields = []
        for key, value in self.hostAddress.items():
            address_fields.append('[@{key}="{value}"]'.format(
                key=key, value=int(value)))

        return './devices/hostdev/source/address{}'.format(
            ''.join(address_fields))

    def getXML(self):
        """
        Create domxml for a host device.

        <hostdev managed="no" mode="subsystem" type="usb">
                <source>
                        <address bus="1" device="2"/>
                </source>
        </hostdev>
        """
        hostdev = self.createXmlElem(hwclass.HOSTDEV, None)
        hostdev.setAttrs(managed='no', mode='subsystem', type='usb')
        source = hostdev.appendChildWithArgs('source')

        source.appendChildWithArgs('address', **self.hostAddress)

        if hasattr(self, 'bootOrder'):
            hostdev.appendChildWithArgs('boot', order=self.bootOrder)

        if hasattr(self, 'address'):
            hostdev.appendChildWithArgs('address', **self.address)

        return hostdev

    @classmethod
    def update_from_xml(cls, vm, device_conf, device_xml):
        alias = core.find_device_alias(device_xml)
        host_address = vmxml.device_address(device_xml)

        # The routine is quite unusual because we cannot directly
        # reconstruct the unique name. Therefore, we first look up
        # corresponding device object address,
        for dev in device_conf:
            if host_address == dev.hostAddress:
                dev.alias = alias
                device = dev.device
                # RHBZ#1215968
                # dev.address = vmxml.device_address(device_xml, 1)

        for dev in vm.conf['devices']:
            if dev['device'] == device:
                dev['alias'] = alias
                # RHBZ#1215968
                # dev['address'] = vmxml.device_address(device_xml, 1)

        # This has an unfortunate effect that we will not be able to look
        # up any previously undefined devices, because there is no easy
        # way of reconstructing the udev name we use as unique id.


class ScsiDevice(core.Base):
    __slots__ = ('address', 'hostAddress', 'bootOrder', '_deviceParams',
                 'name', 'bus_address', 'adapter', 'numa_node')

    def __init__(self, log, **kwargs):
        super(ScsiDevice, self).__init__(log, **kwargs)

        device_params = get_device_params(self.device)
        self.hostAddress = device_params.get('address')
        self.numa_node = None
        self.name = self.device
        self.bus_address, self.adapter = scsi_address_to_adapter(
            self.hostAddress)
        self.is_hostdevice = True

    def setup(self):
        detach_detachable(self.device)

    def teardown(self):
        reattach_detachable(self.device)

    def getXML(self):
        """
        Create domxml for a host device.

        <hostdev managed="no" mode="subsystem" rawio="yes" type="scsi">
            <source>
                <adapter name="scsi_host4"/>
                <address bus="0" target="0" unit="0"/>
            </source>
        </hostdev>
        """
        hostdev = self.createXmlElem(hwclass.HOSTDEV, None)
        hostdev.setAttrs(managed='no', mode='subsystem', type='scsi')
        source = hostdev.appendChildWithArgs('source')

        # This must be done *before* creating the address element, as we need
        # remove the 'host' key from real address.
        source.appendChildWithArgs('adapter', name=self.adapter)
        hostdev.setAttr('rawio', 'yes')

        source.appendChildWithArgs('address', **self.bus_address)

        if hasattr(self, 'bootOrder'):
            hostdev.appendChildWithArgs('boot', order=self.bootOrder)

        if hasattr(self, 'address'):
            hostdev.appendChildWithArgs('address', **self.address)

        return hostdev

    @classmethod
    def update_from_xml(cls, vm, device_conf, device_xml):
        alias = core.find_device_alias(device_xml)
        bus_address = vmxml.device_address(device_xml)
        source = vmxml.find_first(device_xml, 'source')
        adapter = vmxml.find_attr(source, 'adapter', 'name')

        # The routine is quite unusual because we cannot directly
        # reconstruct the unique name. Therefore, we first look up
        # corresponding device object address,
        for dev in device_conf:
            if (hasattr(dev, 'bus_address') and
                    bus_address == dev.bus_address and adapter == dev.adapter):
                dev.alias = alias
                device = dev.device
                dev.address = vmxml.device_address(device_xml, 1)

        for dev in vm.conf['devices']:
            if dev['device'] == device:
                dev['alias'] = alias
                dev['address'] = vmxml.device_address(device_xml, 1)

        # This has an unfortunate effect that we will not be able to look
        # up any previously undefined devices, because there is no easy
        # way of reconstructing the udev name we use as unique id.


class MdevDevice(core.Base):
    __slots__ = ('address', 'mdev_type', 'mdev_uuid', 'mdev_placement',)

    def __init__(self, log, **kwargs):
        super(MdevDevice, self).__init__(log, **kwargs)

        self.mdev_uuid = self.device

    def getXML(self):
        return libvirtxml.make_mdev_element(self.mdev_uuid)

    @classmethod
    def get_identifying_attrs(cls, dev_elem):
        return {
            'devtype': core.dev_class_from_dev_elem(dev_elem),
            'uuid': vmxml.device_address(dev_elem)['uuid']
        }

    @classmethod
    def update_from_xml(cls, vm, device_conf, device_xml):
        alias = core.find_device_alias(device_xml)
        source = vmxml.find_first(device_xml, 'source')
        address = vmxml.find_first(source, 'address')
        uuid = address.attrib['uuid']
        for dev in device_conf:
            if isinstance(dev, MdevDevice) and uuid == dev.mdev_uuid:
                dev.alias = alias
                return

    def setup(self):
        spawn_mdev(self.mdev_type, self.mdev_uuid, self.mdev_placement,
                   self.log)

    def teardown(self):
        despawn_mdev(self.mdev_uuid)


class HostDevice(core.Base):
    __slots__ = ('_device',)

    _DEVICE_MAPPING = {
        'pci': PciDevice,
        'usb_device': UsbDevice,
        'usb': UsbDevice,
        'scsi': ScsiDevice,
        'mdev': MdevDevice,
    }

    def __new__(cls, log, **kwargs):
        try:
            device_params = get_device_params(kwargs['device'])
        except libvirt.libvirtError:
            # TODO: MdevDevice is somewhat generic "UnknownDevice" really, but
            # at this point we don't expect any other device to fail.
            # In future, we should be more careful whether device is mdev or
            # some really unknown device.
            return MdevDevice(log, **kwargs)
        device = cls._DEVICE_MAPPING[
            device_params['capability']](log, **kwargs)
        return device

    @classmethod
    def get_identifying_attrs(cls, dev_elem):
        if core.find_device_type(dev_elem) == 'mdev':
            identifying_class = MdevDevice
        else:
            identifying_class = super(HostDevice, cls)
        return identifying_class.get_identifying_attrs(dev_elem)

    @classmethod
    def from_xml_tree(cls, log, dev, meta):
        params = {
            'device': dev.tag,
            'type': core.find_device_type(dev),
        }
        dev_type = params.get('type')

        try:
            dev_name = _get_device_name(dev, dev_type)
        except KeyError:
            raise NotImplementedError

        core.update_device_params(params, dev)
        _update_hostdev_params(params, dev)
        params['device'] = dev_name
        core.update_device_params_from_meta(params, meta)
        # We cannot access mdev device as it doesn't exist yet.
        if dev_type != 'mdev':
            device_params = get_device_params(dev_name)
            device_class = cls._DEVICE_MAPPING[device_params['capability']]
        else:
            params['mdev_type'] = None
            params['mdev_placement'] = MdevPlacement.COMPACT
            mdev_metadata = meta.get('mdevType')
            if mdev_metadata:
                mdev_info = mdev_metadata.split('|')
                params['mdev_type'] = mdev_info[0]
                if len(mdev_info) > 1:
                    params['mdev_placement'] = mdev_info[1]
            device_class = MdevDevice
        return device_class(log, **params)

    @classmethod
    def update_device_info(cls, vm, device_conf):
        for device_xml in vm.domain.get_device_elements('hostdev'):
            device_type = vmxml.attr(device_xml, 'type')
            try:
                cls._DEVICE_MAPPING[device_type].update_from_xml(
                    vm, device_conf, device_xml)
            except KeyError:
                # Unknown device, be careful.
                continue


def _get_device_name(dev, dev_type):
    src_dev = vmxml.find_first(dev, 'source')
    src_addr = vmxml.device_address(src_dev)
    if dev_type == 'scsi':
        src_addr = _normalize_scsi_address(dev, src_addr)
    elif dev_type == 'pci':
        src_addr = _normalize_pci_address(**src_addr)
    elif dev_type == 'mdev':
        return src_addr['uuid']

    return device_name_from_address(dev_type, src_addr)


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


def _update_hostdev_params(params, dev):
    boot_order = vmxml.find_attr(dev, 'boot', 'order')
    if boot_order:
        params['bootOrder'] = boot_order
    driver = vmxml.find_attr(dev, 'driver', 'name')
    if driver:
        params['driver'] = driver
