#
# Copyright 2014-2019 Red Hat, Inc.
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

import os.path
import threading

from vdsm import constants
from vdsm import utils
from vdsm.common import conv
from vdsm.common import supervdsm
from vdsm.virt import vmxml
from vdsm.virt.utils import cleanup_guest_socket

from . import hwclass
from . import compat


class SkipDevice(Exception):
    pass


class Base(vmxml.Device):
    __slots__ = ('deviceType', 'device', 'alias', 'specParams', 'deviceId',
                 'log', '_deviceXML', 'type', 'custom',
                 'is_hostdevice', 'vmid', '_conf', 'hotunplug_event')

    @classmethod
    def get_identifying_attrs(cls, dev_elem):
        return {
            'devtype': dev_class_from_dev_elem(dev_elem),
            'name': find_device_alias(dev_elem),
        }

    @classmethod
    def from_xml_tree(cls, log, dev, meta):
        """
        Create a device from its libvirt domain XML.

        :param log: logger instance to bind to device.
        :type log: logger, like you get from logging.getLogger()
        :param dev: libvirt domain XML snippet for the device, already parsed
        :type dev: DOM element
        :param meta: device metadata
        :type meta: dict, whose  keys must be python basestrings and whose
         values must be one of: basestring, int, float
        """
        raise NotImplementedError(cls.__name__)

    def get_metadata(self, dev_class):
        """
        Returns two dictionaries: one contains the device attrs, to be
        fed to metadata.Descriptor.device() to match this device
        to its metadata. The other contains the attributes which need
        to be stored in the device metadata area.

        We use one explicit dev_class argument because of
        - oVirt and libvirt use slightly different mapping for device class
          names, and that can't be easily unified for historical reasons;
          (e.g. memballoon vs balloon).
        - using a device instance field (e.g dev.type) instead of dev_class
          works, but it is fragile due to the intricacies of mapping between
          device class parameters and libvirt XML
        - we can use the very same set of value (hwclass.*) for both
          get_metadata() and get_identifying_attrs(), making it obvious and
          more robust.

        NOTE: the metadata infrastructure ensures that the "vmid" key
        is automatically given to device, so to store it is redundant
        and should be avoided.

        NOTE: you should call this method once the device has been
        updated from libvirt data, e.g. when the Device is fully initialized.
        """
        return (
            get_metadata_attrs(self, dev_class),
            get_metadata_values(self),
        )

    def __init__(self, log, **kwargs):
        self.log = log
        self._conf = kwargs
        # ensure the following attributes are added
        self.specParams = {}
        self.custom = {}
        for attr, value in kwargs.items():
            try:
                setattr(self, attr, value)
            except AttributeError:  # skip read-only properties
                self.log.debug('Ignoring param (%s, %s) in %s', attr, value,
                               self.__class__.__name__)
        self._deviceXML = None
        self.is_hostdevice = False
        self.hotunplug_event = threading.Event()

    def __str__(self):
        attrs = [':'.join((a, str(getattr(self, a, None)))) for a in dir(self)
                 if not a.startswith('__')]
        return ' '.join(attrs)

    def config(self):
        """
        Return dictionary of constructor kwargs or None.

        This is used to make a legacy device configuration for this instance.
        Return None in case `update_device_info` already adds the legacy
        configuration.
        """
        return compat.device_config(utils.picklecopy(self._conf))

    @classmethod
    def update_device_info(cls, vm, device_conf):
        """
        Obtain info about this class of devices from libvirt domain and update
        the corresponding device structures.

        :param vm: VM for which the device info should be updated
        :type vm: `class:Vm` instance
        :param device_conf: VM device configuration corresponding to the given
          device.
        :type device_conf: list of dictionaries

        """
        raise NotImplementedError(cls.__name__)

    def setup(self):
        """
        Actions to be executed before VM is started. This method is therefore
        able to modify the final device XML. Not executed in the recovery
        flow.

        It is implementation's obligation to
        * fail without leaving the device in inconsistent state or
        * succeed fully.

        In case of failure, teardown will not be called for device where setup
        failed, only for the devices that were successfully setup before
        the failure.
        """
        pass

    def teardown(self):
        """
        Actions to be executed after the device was destroyed.

        The device can be destroyed either because the whole VM was destroyed
        or because the device was unplugged from the VM.
        """
        pass

    def get_extra_xmls(self):
        """
        Get the auxiliary devices which could be needed by this device.
        Depending on configuration, some devices may require auxiliary devices
        to work properly. Examples are serial device for Console, or SPICE
        channel for Graphics.
        This method serves as a uniform way to provide them.

        It returns an iterable with elements of the same type as the return
        value of getXML.
        """
        return []


class Balloon(Base):
    __slots__ = ('address', 'target', 'minimum',)

    @classmethod
    def from_xml_tree(cls, log, dev, meta):
        params = {
            'device': dev.tag,
            'type': hwclass.BALLOON,
        }
        update_device_params_from_meta(params, meta)
        update_device_params(params, dev)
        params['specParams'] = parse_device_attrs(dev, ('model',))
        return cls(log, **params)

    def __init__(self, *args, **kwargs):
        super(Balloon, self).__init__(*args, **kwargs)
        if not hasattr(self, 'target'):
            self.target = None
        if not hasattr(self, 'minimum'):
            self.minimum = 0

    def getXML(self):
        """
        Create domxml for a memory balloon device.

        <memballoon model='virtio'>
          <address type='pci' domain='0x0000' bus='0x00' slot='0x04'
           function='0x0'/>
        </memballoon>
        """
        m = self.createXmlElem(self.device, None, ['address'])
        m.setAttrs(model=self.specParams['model'])
        return m

    @classmethod
    def update_device_info(cls, vm, device_conf):
        for x in vm.domain.get_device_elements('memballoon'):
            # Ignore balloon devices without address.
            address = find_device_guest_address(x)
            alias = find_device_alias(x)

            for dev in device_conf:
                if address and not hasattr(dev, 'address'):
                    dev.address = address
                if alias and not hasattr(dev, 'alias'):
                    dev.alias = alias

            for dev in vm.conf['devices']:
                if dev['type'] == hwclass.BALLOON:
                    if address and not dev.get('address'):
                        dev['address'] = address
                    if alias and not dev.get('alias'):
                        dev['alias'] = alias


class Console(Base):
    __slots__ = ('_path',)

    CONSOLE_EXTENSION = '.sock'

    @classmethod
    def from_xml_tree(cls, log, dev, meta):
        has_sock = dev.attrib.get('type', 'pty') == 'unix'
        params = {
            'device': dev.tag,
            'type': dev.tag,
        }
        update_device_params(params, dev)
        params['specParams'] = {
            'consoleType': vmxml.find_attr(dev, 'target', 'type'),
            'enableSocket': has_sock,
        }
        update_device_params_from_meta(params, meta)
        params['vmid'] = meta['vmid']
        return cls(log, **params)

    def __init__(self, *args, **kwargs):
        super(Console, self).__init__(*args, **kwargs)
        if not hasattr(self, 'specParams'):
            self.specParams = {}

        if conv.tobool(self.specParams.get('enableSocket', False)):
            self._path = os.path.join(
                constants.P_OVIRT_VMCONSOLES,
                self.vmid + self.CONSOLE_EXTENSION
            )
        else:
            self._path = None

    def prepare(self):
        if self._path:
            supervdsm.getProxy().prepareVmChannel(
                self._path,
                constants.OVIRT_VMCONSOLE_GROUP)

    def cleanup(self):
        if self._path:
            cleanup_guest_socket(self._path)

    @property
    def isSerial(self):
        return self.specParams.get('consoleType', 'virtio') == 'serial'

    def getSerialDeviceXML(self):
        """
        Add a serial port for the console device if it exists and is a
        'serial' type device.

        <serial type='pty'>
            <target port='0'>
        </serial>

        or

        <serial type='unix'>
            <source mode='bind'
              path='/var/run/ovirt-vmconsole-console/${VMID}.sock'/>
            <target port='0'/>
        </serial>
        """
        if self._path:
            s = self.createXmlElem('serial', 'unix')
            s.appendChildWithArgs('source', mode='bind', path=self._path)
        else:
            s = self.createXmlElem('serial', 'pty')
        s.appendChildWithArgs('target', port='0')
        return s

    def getXML(self):
        """
        Create domxml for a console device.

        <console type='pty'>
          <target type='serial' port='0'/>
        </console>

        or:

        <console type='pty'>
          <target type='virtio' port='0'/>
        </console>

        or

        <console type='unix'>
          <source mode='bind' path='/path/to/${vmid}.sock'>
          <target type='virtio' port='0'/>
        </console>
        """
        if self._path:
            m = self.createXmlElem('console', 'unix')
            m.appendChildWithArgs('source', mode='bind', path=self._path)
        else:
            m = self.createXmlElem('console', 'pty')
        consoleType = self.specParams.get('consoleType', 'virtio')
        m.appendChildWithArgs('target', type=consoleType, port='0')
        return m

    @classmethod
    def update_device_info(cls, vm, device_conf):
        for x in vm.domain.get_device_elements('console'):
            # All we care about is the alias
            alias = find_device_alias(x)
            for dev in device_conf:
                if not hasattr(dev, 'alias'):
                    dev.alias = alias

            for dev in vm.conf['devices']:
                if dev['device'] == hwclass.CONSOLE and \
                        not dev.get('alias'):
                    dev['alias'] = alias

    def get_extra_xmls(self):
        if self.isSerial:
            yield self.getSerialDeviceXML()


class Memory(Base):
    __slots__ = ('address', 'size', 'node')

    @classmethod
    def from_xml_tree(cls, log, dev, meta):
        params = {
            'device': dev.tag,
            'type': find_device_type(dev),
        }
        update_device_params(params, dev)
        target = vmxml.find_first(dev, 'target')
        params['size'] = (
            int(vmxml.text(vmxml.find_first(target, 'size'))) // 1024
        )
        params['node'] = vmxml.text(vmxml.find_first(target, 'node'))
        update_device_params_from_meta(params, meta)
        return cls(log, **params)

    def __init__(self, log, **kwargs):
        super(Memory, self).__init__(log, **kwargs)
        # we get size in mb and send in kb
        self.size = int(kwargs.get('size')) * 1024
        self.node = kwargs.get('node')

    @classmethod
    def update_device_info(cls, vm, device_conf):
        conf_aliases = frozenset([getattr(conf, 'alias')
                                  for conf in device_conf
                                  if hasattr(conf, 'alias')])
        dev_aliases = frozenset([dev['alias']
                                 for dev in vm.conf['devices']
                                 if 'alias' in dev])
        for element in vm.domain.get_device_elements('memory'):
            address = find_device_guest_address(element)
            alias = find_device_alias(element)
            node = int(vmxml.text(vmxml.find_first(element, 'node')))
            size = int(vmxml.text(vmxml.find_first(element, 'size')))
            if alias not in conf_aliases:
                for conf in device_conf:
                    if not hasattr(conf, 'alias') and \
                       conf.node == node and \
                       conf.size == size:
                        conf.address = address
                        conf.alias = alias
                        break
            if alias not in dev_aliases:
                for dev in vm.conf['devices']:
                    if dev['type'] == hwclass.MEMORY and \
                       dev.get('alias') is None and \
                       dev.get('node') == node and \
                       dev.get('size', 0) * 1024 == size:
                        dev['address'] = address
                        dev['alias'] = alias
                        break

    def getXML(self):
        """
        <memory model='dimm'>
            <target>
                <size unit='KiB'>524287</size>
                <node>1</node>
            </target>
            <alias name='dimm0'/>
            <address type='dimm' slot='0' base='0x100000000'/>
        </memory>
        """

        mem = self.createXmlElem('memory', None)
        mem.setAttrs(model='dimm')
        target = self.createXmlElem('target', None)
        mem.appendChild(target)
        size = self.createXmlElem('size', None)
        size.setAttrs(unit='KiB')
        size.appendTextNode(str(self.size))
        target.appendChild(size)
        node = self.createXmlElem('node', None)
        node.appendTextNode(str(self.node))
        target.appendChild(node)
        if hasattr(self, 'alias'):
            alias = self.createXmlElem('alias', None)
            alias.setAttrs(name=self.alias)
            mem.appendChild(alias)
        if hasattr(self, 'address'):
            address = self.createXmlElem('address', None)
            address.setAttrs(**self.address)
            mem.appendChild(address)

        return mem


def find_device_alias(dev):
    return vmxml.find_attr(dev, 'alias', 'name')


def find_device_guest_address(dev):
    """
    Find the guest-visible address of a device.

    With respect to vmxml.device_address(), this function will always and only
    look for the guest address; on the other hand, vmxml.device_address() will
    always report the first address it finds.

    Consider this case:
    <dev>
      <source>
        <address>SRC_ADDR</address>
      </source>
    </dev>

    vmxml.device_address() returns SRC_ADDR
    this function will return None

    Consider this case:
    <dev>
      <address>GST_ADDR</address>
      <source>
        <address>SRC_ADDR</address>
      </source>
    </dev>

    vmxml.device_address() returns GST_ADDR
    this function will return GST_ADDR as well.
    """
    addr = dev.find('./address')
    if addr is None:
        return None
    return vmxml.parse_address_element(addr)


def parse_device_attrs(dev, attrs):
    return {
        key: dev.attrib.get(key)
        for key in attrs
        if dev.attrib.get(key)
    }


def get_metadata_attrs(dev_obj, dev_class):
    try:
        name = dev_obj.alias
    except AttributeError:
        # 'log' is a mandatory argument in devices' __init__,
        # so it is good to blow up if that is missing.
        # Everything else is formally optional, even though
        # 'alias' is expected to be present when we call this function.
        dev_obj.log.warning('Cannot find device alias for %s', dev_obj)
        return {}
    else:
        return {'devtype': dev_class, 'name': name}


def get_metadata_values(dev):
    data = {}
    ATTRS = (
        'deviceId',
    )
    update_metadata_from_object(data, dev, ATTRS)
    return data


def find_device_type(dev):
    return dev.attrib.get('type', None) or dev.tag


_LIBVIRT_TO_OVIRT_NAME = {
    'memballoon': hwclass.BALLOON,
}


def dev_class_from_dev_elem(dev_elem):
    dev_type = dev_elem.tag
    return _LIBVIRT_TO_OVIRT_NAME.get(dev_type, dev_type)


def update_device_params(params, dev, attrs=None):
    alias = find_device_alias(dev)
    if alias:
        params['alias'] = alias
    address = find_device_guest_address(dev)
    if address:
        params['address'] = address
    if attrs is not None:
        params.update(parse_device_attrs(dev, attrs))


def get_xml_elem(dev, key, elem, attr):
    value = vmxml.find_attr(dev, elem, attr)
    return {key: value} if value else {}


def update_metadata_from_object(data, dev_obj, keys):
    for key in keys:
        value = getattr(dev_obj, key, None)
        if value is not None:
            data[key] = utils.picklecopy(value)


def update_device_params_from_meta(params, meta):
    device_id = meta.get('deviceId')
    if device_id is not None:
        params['deviceId'] = device_id
