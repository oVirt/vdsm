#
# Copyright 2014 Red Hat, Inc.
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

import collections
import functools
import os
import xml.etree.ElementTree as etree

import libvirt

from . import cpuarch
from . import hooks
from . import libvirtconnection
from . import supervdsm
from . import utils

CAPABILITY_TO_XML_ATTR = {'pci': 'pci',
                          'scsi': 'scsi',
                          'scsi_generic': 'scsi_generic',
                          'usb_device': 'usb'}

_LIBVIRT_DEVICE_FLAGS = {
    'system': libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_SYSTEM,
    'pci': libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_PCI_DEV,
    'usb_device': libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_USB_DEV,
    'usb': libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_USB_INTERFACE,
    'net': libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_NET,
    'scsi_host': libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_SCSI_HOST,
    'scsi_target': libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_SCSI_TARGET,
    'scsi': libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_SCSI,
    'storage': libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_STORAGE,
    'fc_host': libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_FC_HOST,
    'vports': libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_VPORTS,
    'scsi_generic': libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_SCSI_GENERIC,
}

_DATA_PROCESSORS = collections.defaultdict(list)


class PCIHeaderType:
    ENDPOINT = 0
    BRIDGE = 1
    CARDBUS_BRIDGE = 2


class NoIOMMUSupportException(Exception):
    pass


class UnsuitableSCSIDevice(Exception):
    pass


def _data_processor(target_bus='_ANY'):
    """
    Register function as a data processor for device processing code.
    """
    def processor(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            return function(*args, **kwargs)
        _DATA_PROCESSORS[target_bus].append(wrapped)
        return wrapped
    return processor


def is_supported():
    try:
        iommu_groups_exist = bool(len(os.listdir('/sys/kernel/iommu_groups')))
        if cpuarch.is_ppc(cpuarch.real()):
            return iommu_groups_exist

        dmar_exists = bool(len(os.listdir('/sys/class/iommu')))
        return iommu_groups_exist and dmar_exists
    except OSError:
        return False


def _pci_header_type(device_name):
    """
    PCI header type is 1 byte located at 0x0e of PCI configuration space.
    Relevant part of the header structures:

    register (offset)|bits 31-24|bits 23-16 |bits 15-8    |bits 7-0
    0C               |BIST      |Header type|Latency Timer|Cache Line Size

    The structure of type looks like this:

    Bit 7             |Bits 6 to 0
    Multifunction flag|Header Type

    This function should be replaced when [1] is resolved.

    [1]https://bugzilla.redhat.com/show_bug.cgi?id=1317531
    """
    try:
        with open('/sys/bus/pci/devices/{}/config'.format(
                name_to_pci_path(device_name)), 'rb') as f:
            f.seek(0x0e)
            header_type = ord(f.read(1)) & 0x7f
    except IOError:
        # Not a PCI device, we have to treat all of these as assignable.
        return PCIHeaderType.ENDPOINT

    return int(header_type)


def name_to_pci_path(device_name):
    return device_name[4:].replace('_', '.').replace('.', ':', 2)


def scsi_address_to_adapter(scsi_address):
    """
    Read adapter info from scsi host address, and mutate the adress (removing
    'host' key) to conform to libvirt.
    """
    adapter = 'scsi_host{}'.format(scsi_address['host'])
    scsi_address['unit'] = scsi_address['lun']
    del scsi_address['lun']
    del scsi_address['host']

    return {'name': adapter}


def pci_address_to_name(domain, bus, slot, function):
    """
    Convert 4 attributes that identify the pci device on the bus to
    libvirt's pci name: pci_${domain}_${bus}_${slot}_${function}.
    The first 2 characters are hex notation that is unwanted in the name.
    """
    return 'pci_{0}_{1}_{2}_{3}'.format(domain[2:],
                                        bus[2:],
                                        slot[2:],
                                        function[2:])


def _sriov_totalvfs(device_name):
    with open('/sys/bus/pci/devices/{0}/sriov_totalvfs'.format(
            name_to_pci_path(device_name))) as f:
        return int(f.read())


def physical_function_net_name(pf_pci_name):
    """
    takes a pci path of a physical function (e.g. pci_0000_02_00_0) and returns
    the network interface name associated with it (e.g. enp2s0f0)
    """
    devices = list_by_caps()
    libvirt_device_names = [name for name, device in devices.iteritems()
                            if device['params'].get('parent') == pf_pci_name]
    if len(libvirt_device_names) > 1:
        raise Exception('could not determine network name for %s. Possible'
                        'devices: %s' % (pf_pci_name, libvirt_device_names))
    if not libvirt_device_names:
        raise Exception('could not determine network name for %s. There are no'
                        'devices with this parent.' % (pf_pci_name,))

    return libvirt_device_names[0].split('_')[1]


def _process_address(caps, children):
    params = {}
    for cap in children:
        params[cap] = caps.find(cap).text

    return params


def _process_pci_address(caps):
    return _process_address(caps, ('domain', 'bus', 'slot', 'function'))


def _process_scsi_address(caps):
    return _process_address(caps, ('host', 'bus', 'target', 'lun'))


def _process_usb_address(caps):
    return _process_address(caps, ('bus', 'device'))


def _process_storage(caps, params):
    try:
        model = caps.find('model').text
    except AttributeError:
        pass
    else:
        params['product'] = model


def _process_scsi_device_params(device_xml):
    """
    The information we need about SCSI device is contained within multiple
    sysfs devices:

    * vendor and product (not really, more of "human readable name") are
      provided by capability 'storage',
    * path to udev file (/dev/sgX) is provided by 'scsi_generic' capability
      and is required to set correct permissions.

    When reporting the devices via list_by_caps, we don't care if either of
    the devices are not found as the information provided is purely cosmetic.
    If the device is queried in hostdev object creation flow, vendor and
    product are still unnecessary, but udev_path becomes essential.
    """
    def is_parent(device, parent_name):
        try:
            return parent_name == device['params']['parent']
        except KeyError:
            return False

    def find_device_by_parent(device_cap, parent_name):
        for device in list_by_caps(device_cap).values():
            if is_parent(device, parent_name):
                return device

    params = {}

    scsi_name = device_xml.find('name').text
    storage_dev_params = find_device_by_parent(['storage'], scsi_name)
    if storage_dev_params:
        for attr in ('vendor', 'product'):
            try:
                res = storage_dev_params['params'][attr]
            except KeyError:
                pass
            else:
                params[attr] = res

    scsi_generic_dev_params = find_device_by_parent(['scsi_generic'],
                                                    scsi_name)
    if scsi_generic_dev_params:
        params['udev_path'] = scsi_generic_dev_params['params']['udev_path']

    return params


def _process_device_params(device_xml):
    """
    Process device_xml and return dict of found known parameters,
    also doing sysfs lookups for sr-iov related information
    """
    address_processor = {'pci': _process_pci_address,
                         'scsi': _process_scsi_address,
                         'usb_device': _process_usb_address}

    params = {}

    devXML = etree.fromstring(device_xml.decode('ascii', errors='ignore'))
    name = devXML.find('name').text
    if name != 'computer':
        params['parent'] = devXML.find('parent').text

    try:
        driver_name = devXML.find('./driver/name').text
    except AttributeError:
        # No driver exposed by libvirt/sysfs.
        pass
    else:
        params['driver'] = driver_name

    caps = devXML.find('capability')
    params['capability'] = caps.attrib['type']

    data_processors = (_DATA_PROCESSORS['_ANY'] +
                       _DATA_PROCESSORS[params['capability']])

    for data_processor in data_processors:
        params.update(data_processor(devXML))

    for element in ('vendor', 'product', 'interface'):
        elementXML = caps.find(element)
        if elementXML is not None:
            if 'id' in elementXML.attrib:
                params[element + '_id'] = elementXML.attrib['id']
            if elementXML.text:
                params[element] = elementXML.text

    if params['capability'] == 'storage':
        _process_storage(caps, params)

    physfn = caps.find('capability')
    if physfn is not None and physfn.attrib['type'] == 'phys_function' \
            and params['capability'] == 'pci':
        address = physfn.find('address')
        params['physfn'] = pci_address_to_name(**address.attrib)

    is_assignable = None
    if physfn is not None:
        if physfn.attrib['type'] in ('pci-bridge', 'cardbus-bridge'):
            is_assignable = 'false'
    if is_assignable is None:
        is_assignable = str(_pci_header_type(name) ==
                            PCIHeaderType.ENDPOINT).lower()
    params['is_assignable'] = is_assignable

    try:
        udev_path = caps.find('char').text
    except AttributeError:
        pass
    else:
        params['udev_path'] = udev_path

    iommu_group = caps.find('iommuGroup')
    if iommu_group is not None:
        params['iommu_group'] = iommu_group.attrib['number']

    try:
        params['totalvfs'] = _sriov_totalvfs(name)
    except IOError:
        # Device does not support sriov, we can safely go on
        pass

    try:
        params['address'] = address_processor[params['capability']](caps)
    except KeyError:
        # We can somewhat safely ignore missing address as that means we're
        # dealing with device that is not yet supported
        pass

    if params['capability'] == 'scsi':
        params.update(_process_scsi_device_params(devXML))
    return params


def _get_device_ref_and_params(device_name):
    libvirt_device = libvirtconnection.get().\
        nodeDeviceLookupByName(device_name)
    return libvirt_device, _process_device_params(libvirt_device.XMLDesc(0))


def _get_devices_from_libvirt(flags=0):
    """
    Returns all available host devices from libvirt processd to dict
    """
    return dict((device.name(), _process_device_params(device.XMLDesc(0)))
                for device in libvirtconnection.get().listAllDevices(flags))


def list_by_caps(caps=None):
    """
    Returns devices that have specified capability in format
    {device_name: {'params': {'capability': '', 'vendor': '',
                              'vendor_id': '', 'product': '',
                              'product_id': '', 'iommu_group': ''},
                   'vmId': vmId]}

    caps -- list of strings determining devices of which capabilities
            will be returned (e.g. ['pci', 'usb'] -> pci and usb devices)
    """
    devices = {}
    flags = sum([_LIBVIRT_DEVICE_FLAGS[cap] for cap in caps or []])
    libvirt_devices = _get_devices_from_libvirt(flags)

    for devName, params in libvirt_devices.items():
        devices[devName] = {'params': params}

    devices = hooks.after_hostdev_list_by_caps(devices)
    return devices


def get_device_params(device_name):
    _, device_params = _get_device_ref_and_params(device_name)
    return device_params


def detach_detachable(device_name):
    libvirt_device, device_params = _get_device_ref_and_params(device_name)
    capability = CAPABILITY_TO_XML_ATTR[device_params['capability']]

    if capability == 'pci' and utils.tobool(device_params['is_assignable']):
        try:
            iommu_group = device_params['iommu_group']
        except KeyError:
            raise NoIOMMUSupportException('hostdev passthrough without iommu')
        supervdsm.getProxy().appropriateIommuGroup(iommu_group)
        libvirt_device.detachFlags(None)
    elif capability == 'usb':
        supervdsm.getProxy().appropriateUSBDevice(
            device_params['address']['bus'],
            device_params['address']['device'])
    elif capability == 'scsi':
        if 'udev_path' not in device_params:
            raise UnsuitableSCSIDevice

        supervdsm.getProxy().appropriateSCSIDevice(device_name,
                                                   device_params['udev_path'])

    return device_params


def reattach_detachable(device_name):
    libvirt_device, device_params = _get_device_ref_and_params(device_name)
    capability = CAPABILITY_TO_XML_ATTR[device_params['capability']]

    if capability == 'pci' and utils.tobool(device_params['is_assignable']):
        try:
            iommu_group = device_params['iommu_group']
        except KeyError:
            raise NoIOMMUSupportException
        supervdsm.getProxy().rmAppropriateIommuGroup(iommu_group)
        libvirt_device.reAttach()
    elif capability == 'usb':
        supervdsm.getProxy().rmAppropriateUSBDevice(
            device_params['address']['bus'],
            device_params['address']['device'])
    elif capability == 'scsi':
        if 'udev_path' not in device_params:
            raise UnsuitableSCSIDevice

        supervdsm.getProxy().rmAppropriateSCSIDevice(
            device_name, device_params['udev_path'])


def change_numvfs(device_name, numvfs):
    net_name = physical_function_net_name(device_name)
    supervdsm.getProxy().change_numvfs(name_to_pci_path(device_name), numvfs,
                                       net_name)
