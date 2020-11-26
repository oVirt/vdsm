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
# pylint: disable=no-member

from __future__ import absolute_import
from __future__ import division


from vdsm.common import conv
from vdsm.common import hostdev
from vdsm.common import validate
from vdsm.common.hostdev import get_device_params, detach_detachable, \
    pci_address_to_name, reattach_detachable, NoIOMMUSupportException
from vdsm.network import api as net_api
from vdsm.virt import libvirtnetwork
from vdsm.virt import vmxml

from . import compat
from . import core
from . import hwclass

METADATA_KEYS = ('network',)

METADATA_NESTED_KEYS = ('custom', 'portMirroring')

PCI_LINK_UP_TIMEOUT = 5


class UnsupportedAddress(Exception):
    pass


class MissingNetwork(Exception):
    pass


class Interface(core.Base):
    __slots__ = ('nicModel', 'macAddr', 'network', 'bootOrder', 'address',
                 'linkActive', 'portMirroring', 'filter', 'filterParameters',
                 'sndbufParam', 'driver', 'name', 'vlanId', 'hostdev', 'mtu',
                 'numa_node', '_device_params', 'vm_custom', 'port_isolated')

    @classmethod
    def get_identifying_attrs(cls, dev_elem):
        return core.get_xml_elem(dev_elem, 'mac_address', 'mac', 'address')

    def get_metadata(self, dev_class):
        # dev_class unused
        attrs = {'mac_address': self.macAddr}
        data = core.get_metadata_values(self)
        core.update_metadata_from_object(
            data, self, METADATA_KEYS + METADATA_NESTED_KEYS)
        return attrs, data

    @classmethod
    def from_xml_tree(cls, log, dev, meta):
        params = {
            'device': core.find_device_type(dev),
            'type': dev.tag,
            'custom': meta.get('custom', {}),
            'vmid': meta['vmid'],
            'vm_custom': {},
            'specParams': {},
        }
        core.update_device_params(params, dev)
        params.update(core.get_xml_elem(dev, 'macAddr', 'mac', 'address'))
        params.update(core.get_xml_elem(dev, 'nicModel', 'model', 'type'))
        params.update(core.get_xml_elem(dev, 'bootOrder', 'boot', 'order'))
        if params['device'] == 'hostdev':
            params.update(_get_hostdev_params(dev))
        link = vmxml.find_first(dev, 'link', None)
        if link is not None:
            if link.attrib.get('state', 'down') == 'down':
                params['linkActive'] = False
            else:
                params['linkActive'] = True
        vlan = vmxml.find_first(dev, 'vlan', None)
        if vlan is not None:
            params['specParams']['vlanid'] = vmxml.find_attr(
                vlan, 'tag', 'id'
            )
        mtu = vmxml.find_first(dev, "mtu", None)
        if mtu is not None:
            params['mtu'] = int(vmxml.attr(mtu, 'size'))
        port = vmxml.find_first(dev, 'port', None)
        if port is not None:
            params['port_isolated'] = vmxml.attr(port, 'isolated')
        filterref = vmxml.find_first(dev, 'filterref', None)
        if filterref is not None:
            params['filter'] = vmxml.attr(filterref, 'filter')
            params['filterParameters'] = [
                {
                    'name': param.attrib['name'],
                    'value': param.attrib['value'],
                }
                for param in vmxml.find_all(filterref, 'parameter')
            ]
        driver = vmxml.find_first(dev, 'driver', None)
        if driver is not None:
            params['custom'].update(
                core.parse_device_attrs(driver, ('queues',))
            )
        sndbuf = dev.find('./tune/sndbuf')
        if sndbuf is not None:
            params['vm_custom']['sndbuf'] = vmxml.text(sndbuf)
        bandwidth = vmxml.find_first(dev, 'bandwidth', None)
        if bandwidth is not None:
            for mode in ('inbound', 'outbound'):
                elem = vmxml.find_first(bandwidth, mode, None)
                if elem is not None:
                    params['specParams'][mode] = elem.attrib.copy()
        net = (
            meta.get('network', None) or
            vmxml.find_attr(dev, 'source', 'bridge')
        )
        if net is None:
            raise MissingNetwork("no network to join")
        params['network'] = net
        _update_port_mirroring(params, meta)
        core.update_device_params_from_meta(params, meta)
        return cls(log, **params)

    def __init__(self, log, **kwargs):
        # pyLint can't tell that the Device.__init__() will
        # set a nicModel attribute, so modify the kwarg list
        # prior to device init.
        for attr, value in kwargs.items():
            if attr == 'nicModel' and value == 'pv':
                kwargs[attr] = 'virtio'
            elif attr == 'network' and value == '':
                kwargs[attr] = net_api.DUMMY_BRIDGE
        self.portMirroring = []
        self.filterParameters = []
        self.vm_custom = {}
        self.linkActive = True
        self.mtu = None
        self.port_isolated = None
        super(Interface, self).__init__(log, **kwargs)
        self.sndbufParam = False
        self.is_hostdevice = self.device == hwclass.HOSTDEV
        self.vlanId = self.specParams.get('vlanid')
        self._customize()
        if self.is_hostdevice:
            self._device_params = get_device_params(self.hostdev)
            self.numa_node = self._device_params.get('numa_node', None)

    def _customize(self):
        # Customize network device
        self.driver = {}

        vhosts = self._getVHostSettings()
        if vhosts:
            driver_name = vhosts.get(self.network)
            if driver_name:
                self.driver['name'] = driver_name

        try:
            self.driver['queues'] = self.custom['queues']
        except KeyError:
            pass    # interface queues not specified
        else:
            if 'name' not in self.driver:
                self.driver['name'] = 'vhost'

        try:
            self.sndbufParam = self.vm_custom['sndbuf']
        except KeyError:
            pass    # custom_sndbuf not specified

    def _getVHostSettings(self):
        VHOST_MAP = {'true': 'vhost', 'false': 'qemu'}
        vhosts = {}
        vhostProp = self.vm_custom.get('vhost', '')

        if vhostProp != '':
            for vhost in vhostProp.split(','):
                try:
                    vbridge, vstatus = vhost.split(':', 1)
                    vhosts[vbridge] = VHOST_MAP[vstatus.lower()]
                except (ValueError, KeyError):
                    self.log.warning("Unknown vhost format: %s", vhost)

        return vhosts

    def getXML(self):
        """
        Create domxml for network interface.

        <interface type="bridge">
            <mac address="aa:bb:dd:dd:aa:bb"/>
            <model type="virtio"/>
            <source bridge="engine"/>
            [<driver name="vhost/qemu" queues="int"/>]
            [<filterref filter='filter name'>
              [<parameter name='parameter name' value='parameter value'>]
             </filterref>]
            [<tune><sndbuf>0</sndbuf></tune>]
            [<link state='up|down'/>]
            [<bandwidth>
              [<inbound average="int" [burst="int"]  [peak="int"]/>]
              [<outbound average="int" [burst="int"]  [peak="int"]/>]
             </bandwidth>]
            [<alias name="ua-2b418ef2-91d8-4479-88b1-98461192a54e/>]
        </interface>

        -- or -- a slightly different SR-IOV network interface
        <interface type='hostdev' managed='no'>
          <driver name='vfio'/>
          <source>
           <address type='pci' domain='0x0000' bus='0x00' slot='0x07'
           function='0x0'/>
          </source>
          <mac address='52:54:00:6d:90:02'/>
          <vlan>
           <tag id=100/>
          </vlan>
          <address type='pci' domain='0x0000' bus='0x00' slot='0x07'
          function='0x0'/>
          <boot order='1'/>
          [<alias name="ua-2b418ef2-91d8-4479-88b1-98461192a54e/>]
         </interface>

        """
        iface = self.createXmlElem('interface', self.device, ['address'])
        iface.appendChildWithArgs('mac', address=self.macAddr)

        if hasattr(self, 'nicModel'):
            iface.appendChildWithArgs('model', type=self.nicModel)

        if self.is_hostdevice:
            # SR-IOV network interface
            iface.setAttrs(managed='no')
            host_address = self._device_params['address']
            source = iface.appendChildWithArgs('source')
            source.appendChildWithArgs(
                'address', type='pci',
                **validate.normalize_pci_address(**host_address)
            )

            if self.vlanId is not None:
                vlan = iface.appendChildWithArgs('vlan')
                vlan.appendChildWithArgs('tag', id=str(self.vlanId))
        else:
            iface.appendChildWithArgs('source', bridge=self.network)

        if self.mtu is not None:
            iface.appendChildWithArgs('mtu', size=str(self.mtu))

        if self.port_isolated is not None:
            iface.appendChildWithArgs('port', isolated=str(self.port_isolated))

        if hasattr(self, 'filter'):
            filter = iface.appendChildWithArgs('filterref', filter=self.filter)
            _set_parameters_filter(filter, self.filterParameters)

        if hasattr(self, 'linkActive'):
            iface.appendChildWithArgs('link', state='up'
                                      if conv.tobool(self.linkActive)
                                      else 'down')

        if hasattr(self, 'bootOrder'):
            iface.appendChildWithArgs('boot', order=self.bootOrder)

        if self.driver:
            iface.appendChildWithArgs('driver', **self.driver)
        elif self.is_hostdevice:
            iface.appendChildWithArgs('driver', name='vfio')

        if self.sndbufParam:
            tune = iface.appendChildWithArgs('tune')
            tune.appendChildWithArgs('sndbuf', text=self.sndbufParam)

        if 'inbound' in self.specParams or 'outbound' in self.specParams:
            iface.appendChild(self.get_bandwidth_xml(self.specParams))

        if hasattr(self, 'alias'):
            iface.appendChildWithArgs('alias', name=self.alias)

        return iface

    def _set_parameters_filter(self, filter):
        for name, value in self._filter_parameter_map():
            filter.appendChildWithArgs('parameter', name=name, value=value)

    def _filter_parameter_map(self):
        for parameter in self.filterParameters:
            if 'name' in parameter and 'value' in parameter:
                yield parameter['name'], parameter['value']

    @staticmethod
    def get_bandwidth_xml(specParams, oldBandwidth=None):
        """Returns a valid libvirt xml dom element object."""
        bandwidth = vmxml.Element('bandwidth')
        old = {} if oldBandwidth is None else dict(
            (vmxml.tag(elem), elem)
            for elem in vmxml.children(oldBandwidth))
        for key in ('inbound', 'outbound'):
            elem = specParams.get(key)
            if elem is None:  # Use the old setting if present
                if key in old:
                    bandwidth.appendChild(etree_element=old[key])
            elif elem:
                # Convert the values to string for adding them to the XML def
                attrs = dict((key, str(value)) for key, value in elem.items())
                bandwidth.appendChildWithArgs(key, **attrs)
        return bandwidth

    def setup(self):
        if self.is_hostdevice:
            self.log.info('Detaching device %s from the host.' % self.hostdev)
            detach_detachable(self.hostdev)

    def teardown(self):
        if self.is_hostdevice:
            self.log.info('Reattaching device %s to host.' % self.hostdev)
            try:
                pci_path = hostdev.name_to_pci_path(self.hostdev)
                with net_api.wait_for_pci_link_up(
                    pci_path, PCI_LINK_UP_TIMEOUT
                ):
                    # TODO: avoid reattach when Engine can tell free VFs
                    # otherwise
                    reattach_detachable(self.hostdev)
            except NoIOMMUSupportException:
                self.log.exception('Could not reattach device %s back to host '
                                   'due to missing IOMMU support.',
                                   self.hostdev)

    @property
    def _xpath(self):
        """
        Returns xpath to the device in libvirt dom xml
        The path is relative to the root element
        """
        return "./devices/interface/mac[@address='%s']" % self.macAddr

    @classmethod
    def update_device_info(cls, vm, device_conf):
        for x in vm.domain.get_device_elements('interface'):
            devType = vmxml.attr(x, 'type')
            mac = vmxml.find_attr(x, 'mac', 'address')
            alias = core.find_device_alias(x)
            xdrivers = vmxml.find_first(x, 'driver', None)
            if xdrivers is not None:
                driver = core.parse_device_attrs(
                    xdrivers, ('name', 'queues',)
                )
            else:
                driver = {}
            if devType == 'hostdev':
                name = alias
                model = 'passthrough'
            else:
                name = vmxml.find_attr(x, 'target', 'dev')
                model = vmxml.find_attr(x, 'model', 'type')
            if model == 'virtio':
                # Reverse action of the conversion in __init__.
                model = 'pv'

            network = None
            try:
                if vmxml.find_attr(x, 'link', 'state') == 'down':
                    linkActive = False
                else:
                    linkActive = True
            except IndexError:
                linkActive = True
            source = vmxml.find_first(x, 'source', None)
            if source is not None:
                network = vmxml.attr(source, 'bridge')
                if not network:
                    network = libvirtnetwork.netname_l2o(
                        vmxml.attr(source, 'network'))

            address = core.find_device_guest_address(x)

            for nic in device_conf:
                if nic.macAddr.lower() == mac.lower():
                    nic.name = name
                    nic.alias = alias
                    nic.address = address
                    nic.linkActive = linkActive
                    if driver:
                        # If a driver was reported, pass it back to libvirt.
                        # Engine (vm's conf) is not interested in this value.
                        nic.driver.update(driver)
            # Update vm's conf with address for known nic devices
            knownDev = False
            for dev in vm.conf['devices']:
                if (dev['type'] == hwclass.NIC and
                        dev['macAddr'].lower() == mac.lower()):
                    dev['address'] = address
                    dev['alias'] = alias
                    dev['name'] = name
                    dev['linkActive'] = linkActive
                    knownDev = True
            # Add unknown nic device to vm's conf
            if not knownDev:
                nicDev = {'type': hwclass.NIC,
                          'device': devType,
                          'macAddr': mac,
                          'nicModel': model,
                          'address': address,
                          'alias': alias,
                          'name': name,
                          'linkActive': linkActive}
                if network:
                    nicDev['network'] = network
                vm.conf['devices'].append(nicDev)

    def config(self):
        return compat.interface_config(super(Interface, self).config())

    def __repr__(self):
        s = ('<Interface name={name}, type={self.device}, mac={self.macAddr} '
             'at {addr:#x}>')
        # TODO: make name require argument
        return s.format(self=self,
                        name=getattr(self, 'name', None),
                        addr=id(self))

    def update_params(self):
        params = {
            'alias': self.alias,
            'linkActive': self.linkActive,
            'network': self.network,
            'custom': self.custom,
            'specParams': self.specParams,
            'portMirroring': self.portMirroring,
        }
        if self.network == '':
            params['network'] = net_api.DUMMY_BRIDGE
            params['linkActive'] = False
        if self.mtu is not None:
            params['mtu'] = self.mtu
        if self.port_isolated is not None:
            params['port_isolated'] = self.port_isolated
        if self.filter is not None:
            params['filter'] = self.filter
            params['filterParameters'] = self.filterParameters
        return params


def update_port_xml(vnicXML, port_isolated):
    if port_isolated is None:
        try:
            port = vmxml.find_first(vnicXML, 'port')
        except vmxml.NotFound:
            pass
        else:
            vnicXML.remove(port)
    else:
        try:
            port = vmxml.find_first(vnicXML, 'port')
        except vmxml.NotFound:
            port = vnicXML.appendChildWithArgs('port')
        vmxml.set_attr(port, 'isolated', str(port_isolated))


def update_bandwidth_xml(iface, vnicXML, specParams=None):
    if (specParams and
            ('inbound' in specParams or 'outbound' in specParams)):
        oldBandwidth = vmxml.find_first(vnicXML, 'bandwidth', None)
        newBandwidth = iface.get_bandwidth_xml(specParams, oldBandwidth)
        if oldBandwidth is not None:
            vmxml.remove_child(vnicXML, oldBandwidth)
        vmxml.append_child(vnicXML, newBandwidth)


def update_filterref_xml(vnicXML, filterType, filterParameters):
    try:
        filterXML = vmxml.find_first(vnicXML, 'filterref')
    except vmxml.NotFound:
        pass
    else:
        vnicXML.remove(filterXML)

    if filterType:
        filterXML = vnicXML.appendChildWithArgs('filterref', filter=filterType)
        _set_parameters_filter(filterXML, filterParameters)


def _set_parameters_filter(filter, filterParameters):
    for name, value in _filter_parameter_map(filterParameters):
        filter.appendChildWithArgs('parameter', name=name, value=value)


def _filter_parameter_map(filterParameters):
    for parameter in filterParameters:
        if 'name' in parameter and 'value' in parameter:
            yield parameter['name'], parameter['value']


def _update_port_mirroring(params, meta):
    port_mirroring = meta.get('portMirroring', None)
    if port_mirroring is not None:
        params['portMirroring'] = port_mirroring[:]


def _get_hostdev_params(dev):
    src_dev = vmxml.find_first(dev, 'source')
    src_addr = vmxml.device_address(src_dev)
    src_addr_type = src_addr.pop('type', None)
    if src_addr_type != 'pci':
        raise UnsupportedAddress(src_addr_type)

    addr = validate.normalize_pci_address(**src_addr)
    return {
        'hostdev': pci_address_to_name(**addr)
    }
