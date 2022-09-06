# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.common import xmlutils
from vdsm.virt import metadata
from vdsm.virt import vmxml

from . import core
from . import hostdevice
from . import hwclass
from . import lease
from . import network
from . import storage
from . import storagexml


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
    lease.Device.update_device_info(vm, devices[hwclass.LEASE])


_DEVICE_MAPPING = {
    hwclass.DISK: storage.Drive,
    hwclass.NIC: network.Interface,
    hwclass.HOSTDEV: hostdevice.HostDevice,
    hwclass.LEASE: lease.Device,
}


_LEGACY_DEVICE_CLASSES = [
    hwclass.DISK,
    hwclass.NIC,
    hwclass.LEASE,
]


def identify_from_xml_elem(dev_elem):
    dev_name = core.dev_class_from_dev_elem(dev_elem)
    if dev_name not in _DEVICE_MAPPING:
        raise core.SkipDevice()
    return dev_name, _DEVICE_MAPPING[dev_name]


def empty_dev_map():
    return {dev: [] for dev in _LEGACY_DEVICE_CLASSES}


# metadata used by the devices. Unless otherwise specified, type and meaning
# are the same as specified in vdsm-api.yml
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
def dev_map_from_domain_xml(vmid, dom_desc, md_desc, log, noerror=False):
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
    :param noerror: Iff true, don't raise unexpected exceptions on device
      object initialization, just log them.
    :type noerror: bool
    :return: map of initialized devices, map of devices needing refresh.
    :rtype: A device map, in the same format as empty_dev_map() would return.
    """

    log.debug('Initializing device classes from domain XML')
    dev_map = empty_dev_map()
    for dev_type, dev_class, dev_elem in _device_elements(dom_desc, log):
        dev_meta = _get_metadata_from_elem_xml(vmid, md_desc,
                                               dev_class, dev_elem)
        try:
            dev_obj = dev_class.from_xml_tree(log, dev_elem, dev_meta)
        except NotImplementedError:
            log.debug('Cannot initialize %s device: not implemented',
                      dev_type)
        except Exception:
            if noerror:
                log.exception("Device initialization from XML failed")
                dev_elem_xml = xmlutils.tostring(dev_elem, pretty=True)
                log.error("Failed XML: %s", dev_elem_xml)
                log.error("Failed metadata: %s", dev_meta)
            else:
                raise
        else:
            dev_map[dev_type].append(dev_obj)
    log.debug('Initialized %d device classes from domain XML', len(dev_map))
    return dev_map


def dev_elems_from_xml(vm, xml):
    """
    Return device instance building elements from provided XML.

    The XML must contain <devices> element with a single device subelement, the
    one to create the instance for.  Depending on the device kind <metadata>
    element may be required to provide device metadata; the element may and
    needn't contain unrelated metadata.  This function is used in device
    hot(un)plugs.

    Example `xml` value (top element tag may be arbitrary):

      <?xml version='1.0' encoding='UTF-8'?>
      <hotplug>
        <devices>
          <interface type="bridge">
            <mac address="66:55:44:33:22:11"/>
            <model type="virtio" />
            <source bridge="ovirtmgmt" />
            <filterref filter="vdsm-no-mac-spoofing" />
            <link state="up" />
            <bandwidth />
          </interface>
        </devices>
        <metadata xmlns:ns0="http://ovirt.org/vm/tune/1.0"
                  xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
          <ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
            <ovirt-vm:device mac_address='66:55:44:33:22:11'>
              <ovirt-vm:network>test</ovirt-vm:network>
              <ovirt-vm:portMirroring>
                <ovirt-vm:network>network1</ovirt-vm:network>
                <ovirt-vm:network>network2</ovirt-vm:network>
              </ovirt-vm:portMirroring>
            </ovirt-vm:device>
          </ovirt-vm:vm>
        </metadata>
      </hotplug>

    :param xml: XML specifying the device as described above.
    :type xml: basestring
    :returns: Triplet (device_class, device_element, device_meta) where
      `device_class` is the class to be used to create the device instance;
      `device_element` and `device_meta` are objects to be passed as arguments
      to device_class `from_xml_tree` method.
    """
    dom = xmlutils.fromstring(xml)
    devices = vmxml.find_first(dom, 'devices')
    dev_elem = next(vmxml.children(devices))
    _dev_type, dev_class = identify_from_xml_elem(dev_elem)
    meta = vmxml.find_first(dom, 'metadata', None)
    if meta is None:
        md_desc = metadata.Descriptor()
    else:
        md_desc = metadata.Descriptor.from_xml(xmlutils.tostring(meta))
    dev_meta = _get_metadata_from_elem_xml(vm.id, md_desc, dev_class, dev_elem)
    return dev_class, dev_elem, dev_meta


def dev_meta_from_elem(dev_elem, vmid, md_desc):
    _dev_type, dev_class = identify_from_xml_elem(dev_elem)
    return _get_metadata_from_elem_xml(vmid, md_desc, dev_class, dev_elem)


def dev_from_xml(vm, xml):
    """
    Create and return device instance from provided XML.

    `dev_elems_from_xml` is called to extract device building elements and then
    the device instance is created from it and returned.

    :param xml: XML specifying the device as described in `dev_elems_from_xml`.
    :type xml: basestring
    :returns: Device instance created from the provided XML.
    """
    cls, elem, meta = dev_elems_from_xml(vm, xml)
    return cls.from_xml_tree(vm.log, elem, meta)


def storage_device_params_from_domain_xml(vmid, dom_desc, md_desc, log):
    log.debug('Extracting storage devices params from domain XML')
    params = []
    for dev_type, dev_class, dev_elem in _device_elements(dom_desc, log):
        if dev_type != hwclass.DISK:
            log.debug('skipping non-storage device: %r', dev_elem.tag)
            continue

        dev_meta = _get_metadata_from_elem_xml(vmid, md_desc,
                                               dev_class, dev_elem)
        params.append(storagexml.parse(dev_elem, dev_meta))
    log.debug('Extracted %d storage devices params from domain XML',
              len(params))
    return params


def get_metadata(dev_class, dev_obj):
    # storage devices are special, and they need separate treatment
    if dev_class != hwclass.DISK:
        return dev_obj.get_metadata(dev_class)
    return storagexml.get_metadata(dev_obj)


def save_device_metadata(md_desc, dev_map, log):
    log.debug('Saving the device metadata into domain XML')
    count = 0
    for dev_class, dev_objs in dev_map.items():
        for dev_obj in dev_objs:
            attrs, data = get_metadata(dev_class, dev_obj)
            if not data:
                # the device doesn't want to save anything.
                # let's go ahead.
                continue
            elif not attrs:
                # data with no attrs? most likely a bug.
                log.warning('No metadata attrs for %s', dev_obj)
                continue

            with md_desc.device(**attrs) as dev:
                dev.clear()
                dev.update(data)
                count += 1

    log.debug('Saved %d device metadata', count)


def _device_elements(dom_desc, log):
    for dev_elem in vmxml.children(dom_desc.devices):
        try:
            dev_type, dev_class = identify_from_xml_elem(dev_elem)
        except core.SkipDevice:
            pass
        else:
            if dev_type in _LEGACY_DEVICE_CLASSES:
                yield dev_type, dev_class, dev_elem


def _get_metadata_from_elem_xml(vmid, md_desc, dev_class, dev_elem):
    dev_meta = {'vmid': vmid}
    attrs = dev_class.get_identifying_attrs(dev_elem)
    if attrs:
        with md_desc.device(**attrs) as dev_data:
            dev_meta.update(dev_data)
    return dev_meta


def update_guest_disk_mapping(md_desc, disk_devices, guest_disk_mapping, log):
    for serial, value in guest_disk_mapping:
        for d in disk_devices:
            guid = getattr(d, "GUID", None)
            disk_serial = getattr(d, "serial", None)
            image_id = storage.image_id(d.path)
            if image_id and image_id[:20] in serial or \
                    guid and guid[:20] in serial or \
                    disk_serial and disk_serial[:20] in serial:
                d.guestName = value['name']
                log.debug("Guest name of drive %s: %s",
                          image_id, d.guestName)
                attrs, data = storagexml.get_metadata(d)
                with md_desc.device(**attrs) as dev:
                    dev.update(data)
                break
        else:
            if serial[20:]:
                # Silently skip devices that don't appear to have a serial
                # number, such as CD-ROMs devices.
                log.warning("Unidentified guest drive %s: %s",
                            serial, value['name'])
