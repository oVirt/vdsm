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

from vdsm import libvirtconnection


def _parse_device_params(device_xml):
    """
    Process device_xml and return dict of found known parameters
    """
    params = {}

    devXML = etree.fromstring(device_xml)
    name = devXML.find('name').text
    if name != 'computer':
        params['parent'] = devXML.find('parent').text

    caps = devXML.find('capability')
    params['capability'] = caps.attrib['type']

    for element in ('vendor', 'product'):
        elementXML = caps.find(element)
        if elementXML is not None:
            if 'id' in elementXML.attrib:
                params[element + '_id'] = elementXML.attrib['id']
            if elementXML.text:
                params[element] = elementXML.text

    iommu_group = caps.find('iommuGroup')
    if iommu_group is not None:
        params['iommu_group'] = iommu_group.attrib['number']

    return params


def _get_devices_from_vms(vmContainer):
    """
    Scan all running VMs and identify their host devices,
    return mapping of these devices to their VMs in format
    {deviceName: vmId, ...}
    """
    devices = {}

    # loop through VMs and find their host devices
    for vmId, VM in vmContainer.items():
        for device in VM.conf['devices']:
            if device['device'] == 'hostdev':
                name = device['name']
                # Name is always present, if it is not we have encountered
                # unknown situation
                devices[name] = vmId

    return devices


def _get_devices_from_libvirt():
    """
    Returns all available host devices from libvirt parsed to dict
    """
    return dict((device.name(), _parse_device_params(device.XMLDesc(0)))
                for device in libvirtconnection.get().listAllDevices(0))


def list_by_caps(vmContainer, caps=None):
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
    device_to_vm = _get_devices_from_vms(vmContainer)

    for devName, params in libvirt_devices.items():
        if caps and params['capability'] not in caps:
            continue

        devices[devName] = {'params': params}
        if devName in device_to_vm:
            devices[devName]['vmId'] = device_to_vm[devName]

    return devices
