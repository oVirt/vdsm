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

import xml.etree.ElementTree as etree

import hooks
from vdsm import libvirtconnection
from vdsm import supervdsm

CAPABILITY_TO_XML_ATTR = {'pci': 'pci',
                          'scsi': 'scsi',
                          'usb_device': 'usb'}


class NoIOMMUSupportException(Exception):
    pass


def name_to_pci_path(device_name):
    return device_name[4:].replace('_', '.').replace('.', ':', 2)


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
    """takes a pci path of a physical function (e.g. pci_0000_02_00_0) and returns
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


def _parse_address(caps, children):
    params = {}
    for cap in children:
        params[cap] = caps.find(cap).text

    return params


def _parse_pci_address(caps):
    return _parse_address(caps, ('domain', 'bus', 'slot', 'function'))


def _parse_scsi_address(caps):
    return _parse_address(caps, ('host', 'bus', 'target', 'lun'))


def _parse_usb_address(caps):
    return _parse_address(caps, ('bus', 'device'))


def _parse_device_params(device_xml):
    """
    Process device_xml and return dict of found known parameters,
    also doing sysfs lookups for sr-iov related information
    """
    address_parser = {'pci': _parse_pci_address,
                      'scsi': _parse_scsi_address,
                      'usb_device': _parse_usb_address}

    params = {}

    devXML = etree.fromstring(device_xml)
    name = devXML.find('name').text
    if name != 'computer':
        params['parent'] = devXML.find('parent').text

    caps = devXML.find('capability')
    params['capability'] = caps.attrib['type']

    for element in ('vendor', 'product', 'interface'):
        elementXML = caps.find(element)
        if elementXML is not None:
            if 'id' in elementXML.attrib:
                params[element + '_id'] = elementXML.attrib['id']
            if elementXML.text:
                params[element] = elementXML.text

    physfn = caps.find('capability')
    if physfn is not None and physfn.attrib['type'] == 'phys_function' \
            and params['capability'] == 'pci':
        address = physfn.find('address')
        params['physfn'] = pci_address_to_name(**address.attrib)

    iommu_group = caps.find('iommuGroup')
    if iommu_group is not None:
        params['iommu_group'] = iommu_group.attrib['number']

    try:
        params['totalvfs'] = _sriov_totalvfs(name)
    except IOError:
        # Device does not support sriov, we can safely go on
        pass

    try:
        params['address'] = address_parser[params['capability']](caps)
    except KeyError:
        # We can somewhat safely ignore missing address as that means we're
        # dealing with device that is not yet supported
        pass

    return params


def _get_device_ref_and_params(device_name):
    libvirt_device = libvirtconnection.get().\
        nodeDeviceLookupByName(device_name)
    return libvirt_device, _parse_device_params(libvirt_device.XMLDesc(0))


def _get_devices_from_libvirt():
    """
    Returns all available host devices from libvirt parsed to dict
    """
    return dict((device.name(), _parse_device_params(device.XMLDesc(0)))
                for device in libvirtconnection.get().listAllDevices(0))


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
    libvirt_devices = _get_devices_from_libvirt()

    for devName, params in libvirt_devices.items():
        if caps and params['capability'] not in caps:
            continue

        devices[devName] = {'params': params}

    devices = hooks.after_hostdev_list_by_caps(devices)
    return devices


def get_device_params(device_name):
    _, device_params = _get_device_ref_and_params(device_name)
    return device_params


def detach_detachable(device_name):
    libvirt_device, device_params = _get_device_ref_and_params(device_name)
    capability = CAPABILITY_TO_XML_ATTR[device_params['capability']]

    if capability == 'pci':
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

    return device_params


def reattach_detachable(device_name):
    libvirt_device, device_params = _get_device_ref_and_params(device_name)
    capability = CAPABILITY_TO_XML_ATTR[device_params['capability']]

    if capability == 'pci':
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


def change_numvfs(device_name, numvfs):
    net_name = physical_function_net_name(device_name)
    supervdsm.getProxy().changeNumvfs(name_to_pci_path(device_name), numvfs,
                                      net_name)
