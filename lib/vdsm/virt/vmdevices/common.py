#
# Copyright 2008-2017 Red Hat, Inc.
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

from vdsm.virt import vmxml

from . import core
from . import graphics
from . import hostdevice
from . import hwclass
from . import lease
from . import network
from . import storage


def _update_unknown_device_info(vm):
    """
    Obtain info about unknown devices from libvirt domain and update the
    corresponding device structures.  Unknown device is a device that has an
    address but wasn't passed during VM creation request.

    :param vm: VM for which the device info should be updated
    :type vm: `class:Vm` instance

    """
    def isKnownDevice(alias):
        for dev in vm.conf['devices']:
            if dev.get('alias') == alias:
                return True
        return False

    for x in vmxml.children(vm.domain.devices):
        # Ignore empty nodes and devices without address
        if vmxml.find_first(x, 'address', None) is None:
            continue

        alias = core.find_device_alias(x)
        if not isKnownDevice(alias):
            address = vmxml.device_address(x)
            # In general case we assume that device has attribute 'type',
            # if it hasn't dom_attribute returns ''.
            device = vmxml.attr(x, 'type')
            newDev = {'type': vmxml.tag(x),
                      'alias': alias,
                      'device': device,
                      'address': address}
            vm.conf['devices'].append(newDev)


def update_device_info(vm, devices):
    """
    Obtain info about VM devices from libvirt domain and update the
    corresponding device structures.

    :param vm: VM for which the device info should be updated
    :type vm: `class:Vm` instance
    :param devices: Device configuration of the given VM.
    :type devices: dict

    """
    network.Interface.update_device_info(vm, devices[hwclass.NIC])
    storage.Drive.update_device_info(vm, devices[hwclass.DISK])
    core.Sound.update_device_info(vm, devices[hwclass.SOUND])
    graphics.Graphics.update_device_info(vm, devices[hwclass.GRAPHICS])
    core.Video.update_device_info(vm, devices[hwclass.VIDEO])
    core.Controller.update_device_info(vm, devices[hwclass.CONTROLLER])
    core.Balloon.update_device_info(vm, devices[hwclass.BALLOON])
    core.Watchdog.update_device_info(vm, devices[hwclass.WATCHDOG])
    core.Smartcard.update_device_info(vm, devices[hwclass.SMARTCARD])
    core.Rng.update_device_info(vm, devices[hwclass.RNG])
    core.Console.update_device_info(vm, devices[hwclass.CONSOLE])
    hostdevice.HostDevice.update_device_info(vm, devices[hwclass.HOSTDEV])
    core.Memory.update_device_info(vm, devices[hwclass.MEMORY])
    lease.Device.update_device_info(vm, devices[hwclass.LEASE])
    # Obtain info of all unknown devices. Must be last!
    _update_unknown_device_info(vm)


def lookup_device_by_alias(devices, dev_type, alias):
    for dev in devices[dev_type][:]:
        try:
            if dev.alias == alias:
                return dev
        except AttributeError:
            continue
    raise LookupError('Device instance for device identified by alias %s '
                      'and type %s not found' % (alias, dev_type,))


def lookup_conf_by_alias(conf, dev_type, alias):
    for dev_conf in conf[:]:
        try:
            if dev_conf['alias'] == alias and dev_conf['type'] == dev_type:
                return dev_conf
        except KeyError:
            continue
    raise LookupError('Configuration of device identified by alias %s '
                      'and type %s not found' % (alias, dev_type,))


_DEVICE_MAPPING = {
    hwclass.DISK: storage.Drive,
    hwclass.NIC: network.Interface,
    hwclass.SOUND: core.Sound,
    hwclass.VIDEO: core.Video,
    hwclass.GRAPHICS: graphics.Graphics,
    hwclass.CONTROLLER: core.Controller,
    hwclass.GENERAL: core.Generic,
    hwclass.BALLOON: core.Balloon,
    hwclass.WATCHDOG: core.Watchdog,
    hwclass.CONSOLE: core.Console,
    hwclass.REDIR: core.Redir,
    hwclass.RNG: core.Rng,
    hwclass.SMARTCARD: core.Smartcard,
    hwclass.TPM: core.Tpm,
    hwclass.HOSTDEV: hostdevice.HostDevice,
    hwclass.MEMORY: core.Memory,
    hwclass.LEASE: lease.Device,
}


_LIBVIRT_TO_OVIRT_NAME = {
    'memballoon': hwclass.BALLOON,
}


def identify_from_xml_elem(dev_elem):
    dev_type = dev_elem.tag
    dev_name = _LIBVIRT_TO_OVIRT_NAME.get(dev_type, dev_type)
    if dev_name in hwclass.LEGACY_INIT_ONLY:
        raise core.SkipDevice()
    if dev_name not in _DEVICE_MAPPING:
        raise core.SkipDevice()
    return dev_name, _DEVICE_MAPPING[dev_name]


def empty_dev_map():
    return {dev: [] for dev in _DEVICE_MAPPING}


def dev_map_from_dev_spec_map(dev_spec_map, log):
    dev_map = empty_dev_map()

    for dev_type, dev_class in _DEVICE_MAPPING.items():
        for dev in dev_spec_map[dev_type]:
            dev_map[dev_type].append(dev_class(log, **dev))

    return dev_map


# metadata used by the devices. Unless otherwise specified, type and meaning
# are the same as specified in vdsm-api.yml
#
# * graphics.Graphics:
#    = match by: none, implicit matching. Only one SPICE device is allowed
#                and the VNC device ignores the metadata
#    = keys:
#      - display_network
#
#    = example:
#      <metadata xmlns:ovirt-vm='http://ovirt.org/vm/1.0'>
#        <ovirt-vm:vm>
#          <ovirt-vm:device type='graphics'>
#            <ovirt-vm:display_network>ovirtmgmt</ovirt-vm:display_network>
#          </ovirt-vm:device>
#        </ovirt-vm:vm>
#      </metadata>
#
# * network.Interface:
#    = match by: 'mac_address'
#
#    = keys:
#      - network
#
#    = example:
#      <metadata xmlns:ovirt-vm='http://ovirt.org/vm/1.0'>
#        <ovirt-vm:vm>
#          <ovirt-vm:device type='interface' mac_address='...'>
#            <ovirt-vm:network>ovirtmgmt</ovirt-vm:network>
#          </ovirt-vm:device>
#        </ovirt-vm:vm>
#      </metadata>
def dev_map_from_domain_xml(vmid, dom_desc, md_desc, log):
    """
    Create a device map - same format as empty_dev_map from a domain XML
    representation. The domain XML is accessed through a Domain Descriptor.

    :param vmid: UUID of the vm whose devices need to be initialized.
    :type vmid: basestring
    :param dom_desc: domain descriptor to provide access to the domain XML
    :type dom_desc: `class DomainDescriptor`
    :param md_desc: metadata descriptor to provide access to the device
                    metadata
    :type md_desc: `class metadata.Descriptor`
    :param log: logger instance to use for messages, and to pass to device
    objects.
    :type log: logger instance, as returned by logging.getLogger()
    :return: map of initialized devices, map of devices needing refresh.
    :rtype: A device map, in the same format as empty_dev_map() would return.
    """

    dev_map = empty_dev_map()
    for dev_elem in vmxml.children(dom_desc.devices):
        try:
            dev_type, dev_class = identify_from_xml_elem(dev_elem)
        except core.SkipDevice:
            log.debug('skipping unhandled device: %r', dev_elem.tag)
            continue

        dev_meta = {'vmid': vmid}
        attrs = dev_class.get_identifying_attrs(dev_elem)
        if attrs:
            with md_desc.device(**attrs) as dev_data:
                dev_meta.update(dev_data)
        dev_obj = dev_class.from_xml_tree(log, dev_elem, dev_meta)
        dev_map[dev_type].append(dev_obj)
    return dev_map


def replace_devices_xml(domxml, devices_xml):
    devices = vmxml.find_first(domxml, 'devices', None)

    old_devs = [
        dev for dev in vmxml.children(devices)
        if dev.tag in hwclass.TO_REFRESH
    ]
    for old_dev in old_devs:
        vmxml.remove_child(devices, old_dev)

    for dev_class in hwclass.TO_REFRESH:
        for dev in devices_xml[dev_class]:
            vmxml.append_child(devices, etree_child=dev)

    return domxml
