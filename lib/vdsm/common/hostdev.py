#
# Copyright 2014-2020 Red Hat, Inc.
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

import collections
import functools
import hashlib
import json
import logging
import operator
import os
import uuid
import xml.etree.ElementTree as etree

import libvirt
import six

from vdsm.common import commands
from vdsm.common import conv
from vdsm.common import cpuarch
from vdsm.common import exception
from vdsm.common import hooks
from vdsm.common import libvirtconnection
from vdsm.common import supervdsm
from vdsm.common import validate
from vdsm.common.cache import memoized

_MDEV_PATH = '/sys/class/mdev_bus'
_MDEV_FIELDS = ('name', 'description', 'available_instances', 'device_api')
# Arbitrary value (generated on development machine).
_OVIRT_MDEV_NAMESPACE = uuid.UUID('8524b17c-f0ca-44a5-9ce4-66fe261e5986')
_MdevDetail = collections.namedtuple('_MdevDetail', _MDEV_FIELDS)


CAPABILITY_TO_XML_ATTR = collections.defaultdict(
    lambda: 'unknown',

    pci='pci',
    scsi='scsi',
    scsi_generic='scsi_generic',
    usb_device='usb',
)

_LIBVIRT_DEVICE_FLAGS = collections.defaultdict(
    # If the device is not found, let's just treat it like system device. Since
    # those are barely touched, we should be safe.
    lambda: libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_SYSTEM,

    system=libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_SYSTEM,
    pci=libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_PCI_DEV,
    usb_device=libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_USB_DEV,
    usb=libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_USB_INTERFACE,
    net=libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_NET,
    scsi_host=libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_SCSI_HOST,
    scsi_target=libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_SCSI_TARGET,
    scsi=libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_SCSI,
    storage=libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_STORAGE,
    fc_host=libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_FC_HOST,
    vports=libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_VPORTS,
    scsi_generic=libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_SCSI_GENERIC,
)

_DATA_PROCESSORS = collections.defaultdict(list)
_last_alldevices_hash = None
_device_tree_cache = {}
_device_address_to_name_cache = {}


class PCIHeaderType:
    ENDPOINT = 0
    BRIDGE = 1
    CARDBUS_BRIDGE = 2
    UNKNOWN = 99


class Vendor:
    NVIDIA = '0x10de'


class MdevPlacement:
    COMPACT = 'compact'
    SEPARATE = 'separate'


class NoIOMMUSupportException(Exception):
    pass


class UnsuitableSCSIDevice(Exception):
    pass


class _DeviceTreeCache(object):

    def __init__(self, devices):
        self._parent_to_device_params = {}
        # Store a reference so we can look up the params
        self.devices = devices
        self._populate(devices)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._invalidate()

    def get_by_parent(self, capability, parent_name):
        try:
            return self.devices[
                self._parent_to_device_params[capability][parent_name]]
        except KeyError:
            return None

    def _populate(self, devices):
        self._parent_to_device_params = collections.defaultdict(dict)

        for device_name, device_params in devices.items():
            try:
                parent = device_params['parent']
            except KeyError:
                continue

            self._parent_to_device_params[
                device_params['capability']][parent] = device_name

    def _invalidate(self):
        self._parent_to_device_params = {}


@memoized
def _data_processors_map():
    # Unknown devices will only be processed in generic way.
    data_processors_map = collections.defaultdict(
        lambda: _DATA_PROCESSORS['_ANY']
    )

    for capability in _LIBVIRT_DEVICE_FLAGS:
        data_processors_map[capability] = (_DATA_PROCESSORS['_ANY'] +
                                           _DATA_PROCESSORS[capability])
    return data_processors_map


def __device_tree_hash(libvirt_devices):
    """
    The hash generation works iff the order of devices returned from libvirt is
    stable.
    """
    current_hash = hashlib.sha256()
    for _, xml in _each_device_xml(libvirt_devices):
        current_hash.update(xml.encode('utf-8'))

    return current_hash.hexdigest()


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
        return PCIHeaderType.UNKNOWN

    return int(header_type)


def name_to_pci_path(device_name):
    return device_name[4:].replace('_', '.').replace('.', ':', 2)


def _each_device_xml(libvirt_devices):
    for device in libvirt_devices:
        try:
            yield device.name(), device.XMLDesc(0)
        except libvirt.libvirtError:
            # The object still exists, but the underlying device is gone. For
            # us, the device is also gone - ignore it.
            continue


def scsi_address_to_adapter(scsi_address):
    """
    Read device compatible address and adapter info from scsi host address.
    """
    adapter = 'scsi_host{}'.format(scsi_address['host'])

    return ({'unit': scsi_address['lun'],
             'bus': scsi_address['bus'],
             'target': scsi_address['target']},
            adapter)


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
    libvirt_device_names = [name for name, device in six.iteritems(devices) if
                            device['params'].get('parent') == pf_pci_name and
                            device['params'].get('capability') == 'net']
    if len(libvirt_device_names) > 1:
        raise Exception('could not determine network name for %s. Possible'
                        'devices: %s' % (pf_pci_name, libvirt_device_names))
    if not libvirt_device_names:
        raise Exception('could not determine network name for %s. There are no'
                        'devices with this parent.' % (pf_pci_name,))

    return libvirt_device_names[0].split('_')[1]


def _process_address(device_xml, children):
    params = {}
    for cap in children:
        params[cap] = device_xml.find('./capability/{}'.format(cap)).text

    return {'address': params}


@_data_processor('pci')
def _process_pci_address(device_xml):
    return _process_address(device_xml, ('domain', 'bus', 'slot', 'function'))


@_data_processor('scsi')
def _process_scsi_address(device_xml):
    return _process_address(device_xml, ('host', 'bus', 'target', 'lun'))


@_data_processor('usb_device')
def _process_usb_address(device_xml):
    return _process_address(device_xml, ('bus', 'device'))


@_data_processor('pci')
def _process_mdev_params(device_xml):
    # Let's initialize empty description for each mdev type, but be sure to
    # filter empty descriptions later (if we can't parse one of the mdev types,
    # we leave it out).
    mdev = device_xml.find('./capability/capability[@type="mdev_types"]')
    if mdev is None:
        return {}

    supported_types = collections.defaultdict(dict)

    for mdev_type in mdev.findall('type'):
        name = mdev_type.attrib['id']
        name_elem = mdev_type.find('name')
        supported_types[name]['name'] = \
            name if name_elem is None else name_elem.text
        try:
            supported_types[name]['available_instances'] = \
                mdev_type.find('availableInstances').text
        except AttributeError:
            supported_types[name] = {}
            continue
        device_path = device_xml.find('./path').text
        description_path = os.path.join(device_path, 'mdev_supported_types',
                                        name, 'description')
        # The presence of description file is optional and we also
        # shouldn't fail if it can't be read for any reason.
        try:
            description = open(description_path).read().strip()
        except IOError:
            pass
        else:
            supported_types[name]['description'] = description

    # Remove mdev types that we can't handle.
    supported_types = {k: v for k, v in supported_types.items() if v}
    if supported_types:
        return {'mdev': supported_types}
    else:
        return {}


@_data_processor('pci')
def _process_assignability(device_xml):
    is_assignable = None

    physfn = device_xml.find('./capability/capability')

    if physfn is not None:
        if physfn.attrib['type'] in ('pci-bridge', 'cardbus-bridge'):
            is_assignable = 'false'
    if is_assignable is None:
        name = device_xml.find('name').text
        is_assignable = str(_pci_header_type(name) ==
                            PCIHeaderType.ENDPOINT).lower()

    return {'is_assignable': is_assignable}


@_data_processor('scsi_generic')
def _process_udev_path(device_xml):
    try:
        udev_path = device_xml.find('./capability/char').text
    except AttributeError:
        return {}
    else:
        return {'udev_path': udev_path}


@_data_processor()
def _process_driver(device_xml):
    try:
        driver_name = device_xml.find('./driver/name').text
    except AttributeError:
        # No driver exposed by libvirt/sysfs.
        return {}
    else:
        return {'driver': driver_name}


@_data_processor('storage')
def _process_storage(device_xml):
    try:
        model = device_xml.find('./capability/model').text
    except AttributeError:
        return {}
    else:
        return {'product': model}


@_data_processor('pci')
def _process_vfs(device_xml):
    name = device_xml.find('name').text

    try:
        return {'totalvfs': _sriov_totalvfs(name)}
    except IOError:
        # Device does not support sriov, we can safely go on
        return {}


@_data_processor('pci')
def _process_iommu(device_xml):
    iommu_group = device_xml.find('./capability/iommuGroup')
    if iommu_group is not None:
        return {'iommu_group': iommu_group.attrib['number']}
    return {}


@_data_processor('pci')
def _process_physfn(device_xml):
    physfn = device_xml.find('./capability/capability')
    if physfn is not None and physfn.attrib['type'] == 'phys_function':
        address = physfn.find('address')
        return {'physfn': pci_address_to_name(**address.attrib)}
    return {}


@_data_processor()
def _process_productinfo(device_xml):
    params = {}

    capabilities = device_xml.findall('./capability/')
    for capability in capabilities:
        if capability.tag in ('vendor', 'product', 'interface'):
            if 'id' in capability.attrib:
                params[capability.tag + '_id'] = capability.attrib['id']
            if capability.text:
                params[capability.tag] = capability.text

    return params


@_data_processor()
def _process_parent(device_xml):
    name = device_xml.find('name').text

    if name != 'computer':
        return {'parent': device_xml.find('parent').text}

    return {}


@_data_processor('pci')
def _process_numa(device_xml):
    numa_node = device_xml.find('./capability/numa')
    if numa_node is not None:
        return {'numa_node': numa_node.attrib['node']}
    return {}


def _process_scsi_device_params(device_name, cache):
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
    params = {}

    storage_dev_params = cache.get_by_parent('storage', device_name)
    if storage_dev_params:
        for attr in ('vendor', 'product'):
            try:
                res = storage_dev_params[attr]
            except KeyError:
                pass
            else:
                params[attr] = res

    scsi_generic_dev_params = cache.get_by_parent('scsi_generic', device_name)
    if scsi_generic_dev_params:
        params['udev_path'] = scsi_generic_dev_params['udev_path']

    if params.get('udev_path'):
        mapping = _get_udev_block_mapping()
        params['block_path'] = mapping.get(params['udev_path'])

    return params


@memoized
def _get_udev_block_mapping():
    """
    Read system udev path -> block path mapping
    """
    mapping_command = ["lsscsi", "-g"],

    try:
        output = commands.run(*mapping_command).decode('utf-8')
        lines = output.strip().split("\n")
        return {m.split()[-1]: m.split()[-2] for m in lines}
    except Exception as e:
        logging.error(
            "Could not read system udev path -> block path mapping: %s", e)
        return {}


def _process_device_params(device_xml):
    """
    Process device_xml and return dict of found known parameters,
    also doing sysfs lookups for sr-iov related information
    """
    params = {}

    if six.PY2:
        device_xml = device_xml.decode('ascii', errors='ignore')

    devXML = etree.fromstring(device_xml)

    caps = devXML.find('capability')
    params['capability'] = caps.attrib['type']
    params['is_assignable'] = 'true'

    for data_processor in _data_processors_map()[params['capability']]:
        params.update(data_processor(devXML))

    return params


def _get_device_ref_and_params(device_name):
    libvirt_device = libvirtconnection.get().\
        nodeDeviceLookupByName(device_name)
    params = _process_device_params(libvirt_device.XMLDesc(0))

    if params['capability'] != 'scsi':
        return libvirt_device, params

    devices, _ = _get_devices_from_libvirt()
    with _DeviceTreeCache(devices) as cache:
        params.update(_process_scsi_device_params(device_name, cache))

    return libvirt_device, params


def _process_all_devices(libvirt_devices):
    devices = {}
    for name, xml in _each_device_xml(libvirt_devices):
        params = _process_device_params(xml)
        devices[name] = params

    return devices


def _get_devices_from_libvirt(flags=0):
    """
    Returns all available host devices from libvirt processd to dict
    """
    libvirt_devices = libvirtconnection.get().listAllDevices(flags)
    global _last_alldevices_hash
    global _device_tree_cache
    global _device_address_to_name_cache

    if (flags == 0 and
            __device_tree_hash(libvirt_devices) == _last_alldevices_hash):
        return _device_tree_cache, _device_address_to_name_cache

    devices = _process_all_devices(libvirt_devices)
    address_to_name = {}

    with _DeviceTreeCache(devices) as cache:
        for device_name, device_params in devices.items():
            if device_params['capability'] == 'scsi':
                device_params.update(
                    _process_scsi_device_params(device_name, cache))

            _update_address_to_name_map(
                address_to_name, device_name, device_params
            )

    if flags == 0:
        _device_tree_cache = devices
        _device_address_to_name_cache = address_to_name
        _last_alldevices_hash = __device_tree_hash(libvirt_devices)
    return devices, address_to_name


def _update_address_to_name_map(address_to_name, device_name, device_params):
    address_type = CAPABILITY_TO_XML_ATTR[device_params['capability']]
    if 'address' in device_params:
        device_address = _format_address(
            address_type, device_params['address']
        )
        address_to_name[device_address] = device_name


def _each_mdev_device():
    for pci_device in sorted(os.listdir(_MDEV_PATH)):
        yield pci_device


def _each_supported_mdev_type(pci_device):
    path = os.path.join(_MDEV_PATH, pci_device, 'mdev_supported_types')
    for mdev_type in os.listdir(path):
        yield mdev_type, path


def _mdev_type_details(mdev_type, path):
    kwargs = {}
    for field in _MDEV_FIELDS:
        syspath = os.path.join(path, mdev_type, field)
        try:
            with open(syspath, 'r') as f:
                value = f.read().strip()
        except IOError:
            value = ''
        kwargs[field] = value
    if not kwargs['name']:
        kwargs['name'] = mdev_type
    return _MdevDetail(**kwargs)


def _mdev_device_vendor(device):
    with open(os.path.join(_MDEV_PATH, device, 'vendor'), 'r') as f:
        return f.read().strip()


def _mdev_type_devices(mdev_type, path):
    return os.listdir(os.path.join(path, mdev_type, 'devices'))


def _suitable_device_for_mdev_type(target_mdev_type, mdev_placement, log):
    target_device = None

    log.debug("Looking for a mdev of type {}".format(target_mdev_type))
    for device in _each_mdev_device():
        vendor = _mdev_device_vendor(device)
        for mdev_type, path in _each_supported_mdev_type(device):
            if mdev_type != target_mdev_type:
                # If different type is already allocated and the vendor doesn't
                # support different types, skip the device.
                if vendor == '0x10de' and \
                   len(_mdev_type_devices(mdev_type, path)) > 0:
                    target_device = None
                    log.debug("Mdev type {} is different from already "
                              "allocated type {}, skipping device {}"
                              .format(target_mdev_type, mdev_type, device))
                    break
                continue
            elif mdev_placement == MdevPlacement.SEPARATE:
                if len(_mdev_type_devices(mdev_type, path)) > 0:
                    target_device = None
                    log.debug("Mdev {} already used and separate "
                              "placement requested, skipping".format(device))
                    break
            # Make sure to cast to int as the value is read from sysfs.
            if int(
                    _mdev_type_details(mdev_type, path).available_instances
            ) < 1:
                continue

            target_device = device

        if target_device is not None:
            log.debug("Matching mdev found: {}".format(target_device))
            return target_device
        else:
            log.debug("Mdev not suitable: {}".format(device))

    if target_device is None and mdev_placement == MdevPlacement.SEPARATE:
        log.info("Separate mdev placement failed, trying compact placement.")
        return _suitable_device_for_mdev_type(
            target_mdev_type, MdevPlacement.COMPACT, log
        )
    return target_device


def device_name_from_address(address_type, device_address):
    _, address_to_name = _get_devices_from_libvirt()
    address = _format_address(address_type, device_address)
    return address_to_name.get(address)


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
    libvirt_devices, _ = _get_devices_from_libvirt(flags)

    for devName, params in libvirt_devices.items():
        devices[devName] = {'params': params}
    devices.update(list_nvdimms())

    devices = hooks.after_hostdev_list_by_caps(devices)
    return devices


def list_nvdimms():
    """
    Return dictionary of available NVDIMM namespace devices.

    Physical or virtual NVDIMM devices or their parts are organized
    into regions, possibly spanning multiple physical devices, and
    each region can have one or more namespaces. QEMU can be
    instructed to make a virtual NVDIMM device in the guest from a
    namespace NVDIMM device on the host.

    Engine consumes the following parameters for each device:

    - 'device_path' of the namespace device, to know where to access the
      device
    - 'device_size' of the device in bytes, to know what size to set
    - 'align_size' in bytes, to specify the device alignment to libvirt
    - 'mode' of the device, to know whether the device should be set as
      persistent and to inform the user
    - 'numa_node' of the device, to know the NUMA node to put the
      device into

    The returned dictionary is in a host device compatible format, the
    same as in `list_by_caps()`.

    NVDIMM devices are memory devices from the point of view of
    libvirt but we report NVDIMM devices as host devices, of a
    separate 'nvdimm' capability, as this is closer to the way they
    are actually used.
    """
    ndctl_command = ['ndctl', 'list', '--namespaces', '-v']
    try:
        output = commands.run(ndctl_command)
    except Exception:
        logging.exception("Couldn't retrieve NVDIMM device data")
        return {}
    if not output:
        return {}
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        logging.exception("Couldn't parse NVDIMM device data")
        return {}
    nvdimms = {}
    for device in data:
        dev_file = device.get('blockdev') or device.get('chardev')
        if not dev_file:
            try:
                dev_file = device['daxregion']['devices'][0]['chardev']
            except (KeyError, IndexError,):
                logging.warning("No NVDIMM device file: %s", device)
                continue
        parameters = {
            'capability': 'nvdimm',
            'parent': 'computer',
            'device_path': '/dev/' + dev_file,
            'numa_node': device['numa_node'],
            'mode': device['mode'],
            'device_size': int(device['size']),
        }
        if 'align' in device:
            parameters['align_size'] = int(device['align'])
        nvdimms[device['dev']] = {'params': parameters}
    return nvdimms


def get_device_params(device_name):
    _, device_params = _get_device_ref_and_params(device_name)
    return device_params


def detach_detachable(device_name):
    libvirt_device, device_params = _get_device_ref_and_params(device_name)
    capability = CAPABILITY_TO_XML_ATTR[device_params['capability']]

    if capability == 'pci' and conv.tobool(
            device_params['is_assignable']):
        libvirt_device.detachFlags(None)
    elif capability == 'scsi':
        if 'udev_path' not in device_params:
            raise UnsuitableSCSIDevice

    return device_params


def reattach_detachable(device_name, pci_reattach=True):
    libvirt_device, device_params = _get_device_ref_and_params(device_name)
    capability = CAPABILITY_TO_XML_ATTR[device_params['capability']]

    if capability == 'pci' and conv.tobool(
            device_params['is_assignable']):
        if pci_reattach:
            libvirt_device.reAttach()
    elif capability == 'scsi':
        if 'udev_path' not in device_params:
            raise UnsuitableSCSIDevice


def change_numvfs(device_name, numvfs):
    net_name = physical_function_net_name(device_name)
    supervdsm.getProxy().change_numvfs(name_to_pci_path(device_name), numvfs,
                                       net_name)


def spawn_mdev(mdev_type, mdev_uuid, mdev_placement, log):
    device = _suitable_device_for_mdev_type(mdev_type, mdev_placement, log)
    if device is None:
        message = 'vgpu: No device with type {} is available'.format(mdev_type)
        log.error(message)
        raise exception.ResourceUnavailable(message)
    try:
        supervdsm.getProxy().mdev_create(device, mdev_type, mdev_uuid)
    except IOError:
        message = 'vgpu: Failed to create mdev type {}'.format(mdev_type)
        log.error(message)
        raise exception.ResourceUnavailable(message)


def despawn_mdev(mdev_uuid):
    device = None
    for dev in _each_mdev_device():
        if mdev_uuid in os.listdir(os.path.join(_MDEV_PATH, dev)):
            device = dev
            break
    if device is None or mdev_uuid is None:
        raise exception.ResourceUnavailable('vgpu: No mdev found')
    try:
        supervdsm.getProxy().mdev_delete(device, mdev_uuid)
    except IOError:
        # This is destroy flow, we can't really fail
        pass


def _format_address(dev_type, address):
    if dev_type == 'pci':
        address = validate.normalize_pci_address(**address)
    ret = '_'.join(
        '{}{}'.format(key, value) for key, value in sorted(
            address.items(),
            key=operator.itemgetter(0)
        )
    )
    return '{}_{}'.format(dev_type, ret)
