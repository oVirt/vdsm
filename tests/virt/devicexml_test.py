#
# Copyright 2017-2020 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import logging

from vdsm.virt.domain_descriptor import DomainDescriptor
from vdsm.virt.vmdevices import lookup
from vdsm.virt import metadata
from vdsm.virt import vmdevices
from vdsm.virt import vmxml
from vdsm.common import hostdev
from vdsm.common import xmlutils

from monkeypatch import MonkeyPatchScope, MonkeyPatch
from testlib import permutations, expandPermutations
from testlib import read_data
from testlib import XMLTestCase

import vmfakecon as fake
import hostdevlib
import pytest


@expandPermutations
class DeviceToXMLTests(XMLTestCase):

    PCI_ADDR = \
        'bus="0x00" domain="0x0000" function="0x0" slot="0x03" type="pci"'
    PCI_ADDR_DICT = {'slot': '0x03', 'bus': '0x00', 'domain': '0x0000',
                     'function': '0x0', 'type': 'pci'}

    def setUp(self):
        self.log = logging.getLogger('test.virt')
        self.conf = {
            'vmName': 'testVm',
            'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
            'smp': '8',
            'maxVCpus': '160',
            'memSize': '1024',
            'memGuaranteedSize': '512',
        }

    def test_memory_device(self):
        memoryXML = """<memory model='dimm'>
            <target>
                <size unit='KiB'>1048576</size>
                <node>0</node>
            </target>
        </memory>
        """
        params = {'device': 'memory', 'type': 'memory',
                  'size': 1024, 'node': 0}
        self.assertXMLEqual(vmdevices.core.memory_xml(params), memoryXML)

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_interface(self):
        interfaceXML = """
            <interface type="bridge"> <address %s/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge="ovirtmgmt"/>
                <filterref filter="no-mac-spoofing"/>
                <link state="up"/>
                <boot order="1"/>
                <driver name="vhost" queues="7"/>
                <tune>
                    <sndbuf>0</sndbuf>
                </tune>
                <bandwidth>
                    <inbound average="1000" burst="1024" peak="5000"/>
                    <outbound average="128" burst="256"/>
                </bandwidth>
            </interface>""" % self.PCI_ADDR

        dev = {'nicModel': 'virtio', 'macAddr': '52:54:00:59:F5:3F',
               'network': 'ovirtmgmt', 'address': self.PCI_ADDR_DICT,
               'device': 'bridge', 'type': 'interface',
               'bootOrder': '1', 'filter': 'no-mac-spoofing',
               'specParams': {'inbound': {'average': 1000, 'peak': 5000,
                                          'burst': 1024},
                              'outbound': {'average': 128, 'burst': 256}},
               'custom': {'queues': '7'},
               'vm_custom': {'vhost': 'ovirtmgmt:true', 'sndbuf': '0'},
               }
        iface = vmdevices.network.Interface(self.log, **dev)
        self.assertXMLEqual(xmlutils.tostring(iface.getXML()), interfaceXML)

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_interface_filter_parameters(self):
        interfaceXML = """
            <interface type="bridge"> <address %s/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge="ovirtmgmt"/>
                <filterref filter="clean-traffic">
                    <parameter name='IP' value='10.0.0.1'/>
                    <parameter name='IP' value='10.0.0.2'/>
                </filterref>
                <link state="up"/>
                <boot order="1"/>
                <driver name="vhost"/>
                <tune>
                    <sndbuf>0</sndbuf>
                </tune>
            </interface>""" % self.PCI_ADDR

        dev = {
            'nicModel': 'virtio', 'macAddr': '52:54:00:59:F5:3F',
            'network': 'ovirtmgmt', 'address': self.PCI_ADDR_DICT,
            'device': 'bridge', 'type': 'interface',
            'bootOrder': '1', 'filter': 'clean-traffic',
            'filterParameters': [
                {'name': 'IP', 'value': '10.0.0.1'},
                {'name': 'IP', 'value': '10.0.0.2'},
            ],
            'vm_custom': {'vhost': 'ovirtmgmt:true', 'sndbuf': '0'},
        }

        iface = vmdevices.network.Interface(self.log, **dev)
        self.assertXMLEqual(xmlutils.tostring(iface.getXML()), interfaceXML)

    @MonkeyPatch(vmdevices.network.net_api, 'net2vlan', lambda x: 101)
    def test_interface_on_ovs_with_vlan(self):
        proxy = FakeProxy(ovs_bridge={
            'ovn_net_1': {
                'name': 'vdsmbr_fffffff',
                'dpdk_enabled': False
            }
        })
        interfaceXML = """
            <interface type="bridge">
                <address %s/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge="vdsmbr_fffffff"/>
                <virtualport type="openvswitch" />
                <vlan>
                    <tag id="101" />
                </vlan>
                <filterref filter="no-mac-spoofing"/>
                <link state="up"/>
                <boot order="1"/>
                <driver name="vhost" queues="7"/>
                <tune>
                    <sndbuf>0</sndbuf>
                </tune>
            </interface>""" % self.PCI_ADDR

        dev = {
            'nicModel': 'virtio',
            'macAddr': '52:54:00:59:F5:3F',
            'network': 'ovn_net_1',
            'address': self.PCI_ADDR_DICT,
            'device': 'bridge',
            'type': 'interface',
            'bootOrder': '1',
            'filter': 'no-mac-spoofing',
            'custom': {'queues': '7'},
            'vm_custom': {'vhost': 'ovirtmgmt:true', 'sndbuf': '0'},
        }
        with MonkeyPatchScope([
            (vmdevices.network.supervdsm, 'getProxy', lambda: proxy)
        ]):
            iface = vmdevices.network.Interface(self.log, **dev)
            self.assertXMLEqual(xmlutils.tostring(iface.getXML()),
                                interfaceXML)

    @permutations([
        # base_spec_params:
        [{}],
        [{'inbound': {'average': 512}, 'outbound': {'average': 512}}],
    ])
    def test_update_bandwidth_xml(self, base_spec_params):
        specParams = {
            'inbound': {
                'average': 1000,
                'peak': 5000,
                'floor': 200,
                'burst': 1024,
            },
            'outbound': {
                'average': 128,
                'peak': 256,
                'burst': 256,
            },
        }
        conf = {
            'device': 'network',
            'macAddr': 'fake',
            'network': 'default',
            'specParams': base_spec_params,
        }
        XML = u"""
        <interface type='network'>
          <mac address="fake" />
          <source bridge='default'/>
          <link state="up"/>
          <bandwidth>
            <inbound average='1000' peak='5000' floor='200' burst='1024'/>
            <outbound average='128' peak='256' burst='256'/>
          </bandwidth>
        </interface>
        """
        with MonkeyPatchScope([
            (vmdevices.network.supervdsm, 'getProxy', lambda: FakeProxy())
        ]):
            dev = vmdevices.network.Interface(self.log, **conf)
            vnic_xml = dev.getXML()
            vmdevices.network.update_bandwidth_xml(dev, vnic_xml, specParams)
            self.assertXMLEqual(xmlutils.tostring(vnic_xml), XML)


@expandPermutations
class ParsingHelperTests(XMLTestCase):

    ADDR = {
        'domain': '0x0000',
        'bus': '0x05',
        'slot': '0x11',
        'function': '0x3',
    }

    ALIAS = 'test0'

    def test_address_alias(self):
        params = {'alias': self.ALIAS}
        params.update(self.ADDR)
        XML = u"""<device type='fake'>
          <address domain='{domain}' bus='{bus}'
            slot='{slot}' function='{function}'/>
          <alias name='{alias}'/>
        </device>""".format(**params)
        dev = xmlutils.fromstring(XML)
        found_addr = vmdevices.core.find_device_guest_address(dev)
        found_alias = vmdevices.core.find_device_alias(dev)
        assert found_addr == self.ADDR
        assert found_alias == self.ALIAS

    def test_missing_address(self):
        XML = u"""<device type='fake'>
          <alias name='{alias}'/>
        </device>""".format(alias=self.ALIAS)
        dev = xmlutils.fromstring(XML)
        found_addr = vmdevices.core.find_device_guest_address(dev)
        found_alias = vmdevices.core.find_device_alias(dev)
        assert found_addr is None
        assert found_alias == self.ALIAS

    def test_missing_alias(self):
        params = self.ADDR.copy()
        XML = u"""<device type='fake'>
          <address domain='{domain}' bus='{bus}'
            slot='{slot}' function='{function}'/>
        </device>""".format(**params)
        dev = xmlutils.fromstring(XML)
        found_addr = vmdevices.core.find_device_guest_address(dev)
        found_alias = vmdevices.core.find_device_alias(dev)
        assert found_addr == self.ADDR
        assert found_alias == ''

    def test_missing_address_alias(self):
        XML = u"<device type='fake' />"
        dev = xmlutils.fromstring(XML)
        found_addr = vmdevices.core.find_device_guest_address(dev)
        found_alias = vmdevices.core.find_device_alias(dev)
        assert found_addr is None
        assert found_alias == ''

    def test_attrs(self):
        XML = u"<device type='fake' />"
        attrs = vmdevices.core.parse_device_attrs(
            xmlutils.fromstring(XML), ('type',)
        )
        assert attrs == {'type': 'fake'}

    def test_attrs_missing(self):
        XML = u"<device type='fake' />"
        attrs = vmdevices.core.parse_device_attrs(
            xmlutils.fromstring(XML), ('type', 'foo')
        )
        assert attrs == {'type': 'fake'}

    def test_attrs_partial(self):
        XML = u"<device foo='bar' ans='42' fizz='buzz' />"
        attrs = vmdevices.core.parse_device_attrs(
            xmlutils.fromstring(XML), ('foo', 'fizz')
        )
        assert attrs == {'foo': 'bar', 'fizz': 'buzz'}

    @permutations([
        # xml_data, dev_type
        [u'''<interface type='network' />''', 'network'],
        [u'''<console type="pty" />''', 'pty'],
        [u'''<controller type='usb' index='0' />''', 'usb'],
        [u'''<sound model="ac97"/>''', 'sound'],
        [u'''<tpm model='tpm-tis'/>''', 'tpm'],
    ])
    def test_find_device_type(self, xml_data, dev_type):
        assert dev_type == \
            vmdevices.core.find_device_type(xmlutils.fromstring(xml_data))

    @permutations([
        # xml_data, alias
        # well formed XMLs
        [u'''<interface><alias name="net0" /></interface>''', 'net0'],
        [u'''<console type="pty" />''', ''],
        # malformed XMLs
        [u'''<controller><alias>foobar</alias></controller>''', ''],
    ])
    def test_find_device_alias(self, xml_data, alias):
        assert alias == \
            vmdevices.core.find_device_alias(xmlutils.fromstring(xml_data))

    @permutations([
        # xml_data, address
        [u'''<interface>
                <source>
                  <address type='pci' domain='0x0000' bus='0x00'
                   slot='0x04' function='0x0'/>
                </source>
              </interface>''',
         None],
        [u'''<interface>
                <address type='pci' domain='0x0000' bus='0x00'
                  slot='0x04' function='0x0'/>
              </interface>''',
         {'bus': '0x00', 'domain': '0x0000',
          'function': '0x0', 'slot': '0x04', 'type': 'pci'}],
        [u'''<interface>
                <address type='pci' domain='0x0000' bus='0x00'
                  slot='0x04' function='0x0'/>
                <source>
                  <address type='pci' domain='0x0000' bus='0x02'
                    slot='0x02' function='0x5'/>
                </source>
              </interface>''',
         {'bus': '0x00', 'domain': '0x0000',
          'function': '0x0', 'slot': '0x04', 'type': 'pci'}],
    ])
    def test_find_device_guest_address(self, xml_data, address):
        assert address == \
            vmdevices.core.find_device_guest_address(
                xmlutils.fromstring(xml_data)
            )


class FakeProxy(object):

    def __init__(self, ovs_bridge=None):
        self._ovs_bridge = {} if ovs_bridge is None else ovs_bridge

    def ovs_bridge(self, name):
        return self._ovs_bridge.get(name, None)

    def appropriateHwrngDevice(self, vmid):
        pass

    def rmAppropriateHwrngDevice(self, vmid):
        pass


# the alias is not rendered by getXML, so having it would make
# the test fail
_CONTROLLERS_XML = [
    [u"<controller type='virtio-serial' index='0' ports='16'>"
     u"<address type='pci' domain='0x0000' bus='0x00'"
     u" slot='0x07' function='0x0'/>"
     u"</controller>"],
    [u"<controller type='usb' index='0'>"
     u"<address type='pci' domain='0x0000' bus='0x00'"
     u" slot='0x01' function='0x2'/>"
     u"</controller>"],
    [u"<controller type='pci' index='0' model='pci-root' />"],
    [u"<controller type='ccid' index='0' />"],
    [u"<controller type='ide' index='0'/>"],
    [u"<controller type='scsi' index='0' model='virtio-scsi'>"
     u"<address type='pci' domain='0x0000' bus='0x00' slot='0x0b'"
     u" function='0x0'/>"
     u"<driver iothread='4'/>"
     u"</controller>"],
]

_TRANSIENT_STORAGE_TEST_DATA = [
    [u'''<disk device="disk" snapshot="no" type="block">
        <source dev="/var/lib/vdsm/transient">
            <seclabel model="dac" relabel="no" type="none" />
        </source>
        <target bus="scsi" dev="sda"/>
        <serial>54-a672-23e5b495a9ea</serial>
        <driver cache="writethrough" error_policy="stop"
                io="native" name="qemu" type="qcow2"/>
    </disk>''',
     {'shared': 'transient'}],
    [u'''<disk device="disk" snapshot="no" type="file">
            <source file="/var/lib/vdsm/transient"/>
            <target bus="scsi" dev="sda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="writethrough" error_policy="stop"
                    io="threads" name="qemu" type="qcow2"/>
        </disk>''',
     {'shared': 'transient'}]
]

_STORAGE_TEST_DATA = [
    [u'''<disk device="disk" snapshot="no" type="block">
            <source dev="/path/to/volume">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target bus="virtio" dev="vda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="none" discard="unmap" error_policy="stop"
                    io="native" name="qemu" type="raw"/>
        </disk>''',
     {}],
    [u'''<disk device="disk" snapshot="no" type="block">
            <source dev="/path/to/volume">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target bus="virtio" dev="vda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="none" discard="unmap" error_policy="enospace"
                    io="native" name="qemu" type="raw"/>
        </disk>''',
     {}],
    [u'''<disk device="disk" snapshot="no" type="block">
            <source dev="/path/to/volume">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target bus="virtio" dev="vda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="none" error_policy="stop"
                    io="native" name="qemu" type="raw"/>
        </disk>''',
     {}],
    [u'''<disk device="disk" snapshot="no" type="file">
            <source file="/path/to/volume">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target bus="virtio" dev="vda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="none" error_policy="stop"
                    io="threads" name="qemu" type="raw"/>
        </disk>''',
     {}],
    [u'''<disk device="lun" sgio="unfiltered" snapshot="no" type="block">
            <source dev="/dev/mapper/lun1">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target bus="scsi" dev="sda"/>
            <driver cache="none" error_policy="stop"
                    io="native" name="qemu" type="raw"/>
        </disk>''',
     {}],
    [u'''<disk device="disk" snapshot="no" type="network">
            <source name="poolname/volumename" protocol="rbd">
                <host name="1.2.3.41" port="6789" transport="tcp"/>
                <host name="1.2.3.42" port="6789" transport="tcp"/>
            </source>
            <target bus="virtio" dev="vda"/>
            <driver cache="none" error_policy="stop"
                    io="threads" name="qemu" type="raw"/>
        </disk>''',
     {}],
    [u'''<disk device="disk" snapshot="no" type="network">
            <source name="poolname/volumename" protocol="rbd">
                <host name="1.2.3.41" port="6789" transport="tcp"/>
                <host name="1.2.3.42" port="6789" transport="tcp"/>
            </source>
            <auth username="cinder">
                <secret type="ceph" uuid="abcdef"/>
            </auth>
            <target bus="virtio" dev="vda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="none" error_policy="stop"
                    io="threads" name="qemu" type="raw"/>
        </disk>''',
     {}],
    [u'''<disk device="lun" sgio="unfiltered" snapshot="no" type="block">
            <address bus="0" controller="0" target="0" type="drive" unit="0" />
            <source dev="/dev/mapper/36001405b3b7829f14c1400d925eefebb">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target bus="scsi" dev="sda" />
            <driver cache="none" error_policy="stop" io="native"
                    name="qemu" type="raw" />
        </disk>''',
     {}],
    [u'''<disk device="cdrom" snapshot="no" type="file">
            <source file="/run/vdsm/payload/{guid}.{hashsum}.img"
                startupPolicy="optional">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target bus="ide" dev="hdd" />
            <readonly />
            <driver error_policy="report" name="qemu" type="raw" />
        </disk>'''.format(guid='8a1dc504-9d00-48f3-abdc-c70404e6f7e2',
                          hashsum='4137dc5fb55e021fbfd2653621d9d194'),
     {}],
    # cdrom from Engine 4.2.0, using error_policy="report"
    [u'''<disk type="file" device="cdrom" snapshot="no">
            <address bus="1" controller="0" unit="0" type="drive" target="0"/>
            <source file="" startupPolicy="optional">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target dev="hdc" bus="ide"/>
            <readonly/>
            <driver name="qemu" type="raw" error_policy="report"/>
         </disk>''',
     {}],
    [u'''<disk device="disk" snapshot="no" type="block">
            <source dev="/path/to/volume">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target bus="virtio" dev="vda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="none" discard="unmap" error_policy="stop"
                    io="native" name="qemu" type="raw"/>
            <iotune>
                <read_iops_sec>400000</read_iops_sec>
                <total_bytes_sec>10000000</total_bytes_sec>
                <write_iops_sec>100000</write_iops_sec>
            </iotune>
        </disk>''',
     {}],
    # disk from Engine 4.2.0
    [u'''<disk snapshot="no" type="block" device="disk">
            <address bus="0" controller="0" unit="0" type="drive" target="0"/>
            <source dev="/rhev/data-center/mnt/blockSD/a/images/b/c">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target dev="sda" bus="scsi"/>
            <serial>d591482b-eb24-47bd-be07-082c115d11f4</serial>
            <boot order="1"/>
            <driver name="qemu" io="native" type="qcow2"
              error_policy="stop" cache="none"/>
            <alias name="ua-58ca6050-0134-00d6-0053-000000000388"/>
        </disk>''',
     {}],
    # cache attribute taken from XML for non-transient disks
    [u'''<disk device="disk" snapshot="no" type="file">
            <source file="/path/to/volume">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target bus="sata" dev="sda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="writethrough" error_policy="enospace"
                    io="threads" name="qemu" type="raw"/>
        </disk>''',
     {}],
]


_HOSTDEV_XML = [
    [u'''<hostdev mode='subsystem' type='pci' managed='no'>
      <source>
        <address domain='0x0000' bus='0x00' slot='0x19' function='0x0'/>
      </source>
      <boot order='1'/>
    </hostdev>'''],
    [u'''<hostdev managed="no" mode="subsystem" type="usb">
      <source>
        <address bus="1" device="1"/>
      </source>
    </hostdev>'''],
    [u'''<hostdev managed="no" mode="subsystem" rawio="yes" type="scsi">
      <source>
        <adapter name="scsi_host0"/>
        <address bus="0" target="0" unit="0"/>
      </source>
    </hostdev>'''],
    [u'''<hostdev mode='subsystem' type='pci' managed='no'>
      <source>
        <address domain='0x0000' bus='0x00' slot='0x19' function='0x0'/>
      </source>
      <address type='pci' domain='0x0000' bus='0x00'
        slot='0x03' function='0x0'/>
    </hostdev>'''],
]

_MDEV_XML = u'''<hostdev mode="subsystem" model="vfio-pci" type="mdev">
  <source>
    <address uuid="8cb81aac-0c99-3e14-8f48-17c7c7d1c538"/>
  </source>
</hostdev>
'''


@expandPermutations
class DeviceXMLRoundTripTests(XMLTestCase):

    def test_base_not_implemented(self):
        # simplified version of channel XML, only for test purposes.
        # this should never be seen in the wild
        generic_xml = '<channel type="spicevmc" />'
        try:
            vmdevices.core.Base.from_xml_tree(
                self.log,
                xmlutils.fromstring(generic_xml),
                meta={'vmid': 'VMID'}
            )
        except NotImplementedError as exc:
            assert vmdevices.core.Base.__name__ == \
                str(exc)
        except Exception as ex:
            raise AssertionError('from_xml_tree raise unexpected %s', ex)
        else:
            raise AssertionError('from_xml_tree implemented')

    def test_lease(self):
        lease_xml = u'''<lease>
            <key>12523e3d-ad22-410c-8977-d2a7bf458a65</key>
            <lockspace>c2a6d7c8-8d81-4e01-9ed4-7eb670713448</lockspace>
            <target offset="1048576"
                    path="/dev/c2a6d7c8-8d81-4e01-9ed4-7eb670713448/leases"/>
        </lease>'''
        self._check_roundtrip(vmdevices.lease.Device, lease_xml)

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_interface(self):
        interface_xml = u'''
            <interface type="bridge">
                <address bus="0x00" domain="0x0000"
                    function="0x0" slot="0x03" type="pci"/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge="ovirtmgmt"/>
                <filterref filter="clean-traffic">
                    <parameter name='IP' value='10.0.0.1'/>
                    <parameter name='IP' value='10.0.0.2'/>
                </filterref>
                <link state="up"/>
                <boot order="1"/>
                <driver name="vhost" queues="7"/>
                <tune>
                    <sndbuf>0</sndbuf>
                </tune>
                <bandwidth>
                    <inbound average="1000" burst="1024" peak="5000"/>
                    <outbound average="128" burst="256"/>
                </bandwidth>
            </interface>'''
        meta = {'vmid': 'VMID'}
        self._check_roundtrip(
            vmdevices.network.Interface, interface_xml, meta=meta)

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_interface_mtu(self):
        interface_xml = u'''
            <interface type="bridge">
                <address bus="0x00" domain="0x0000"
                    function="0x0" slot="0x03" type="pci"/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge="ovirtmgmt"/>
                <mtu size="1492"/>
                <link state="up"/>
                <boot order="1"/>
            </interface>'''
        meta = {'vmid': 'VMID'}
        self._check_roundtrip(
            vmdevices.network.Interface, interface_xml, meta=meta)

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_interface_isolated(self):
        interface_xml = u'''
            <interface type="bridge">
                <address bus="0x00" domain="0x0000"
                    function="0x0" slot="0x03" type="pci"/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge="ovirtmgmt"/>
                <port isolated="yes"/>
                <link state="up"/>
            </interface>'''
        meta = {'vmid': 'VMID'}
        self._check_roundtrip(
            vmdevices.network.Interface, interface_xml, meta=meta)

    @permutations([
        # link state
        ('up',),
        ('down',),
    ])
    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_interface_link_state(self, link_state):
        interface_xml = u'''
            <interface type="bridge">
                <address bus="0x00" domain="0x0000"
                    function="0x0" slot="0x03" type="pci"/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge="ovirtmgmt"/>
                <link state="{link_state}"/>
                <boot order="1"/>
            </interface>'''.format(link_state=link_state)
        meta = {'vmid': 'VMID'}
        self._check_roundtrip(
            vmdevices.network.Interface, interface_xml, meta=meta)

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_interface_empty_bridge(self):
        interface_xml = u'''
            <interface type="bridge">
                <address bus="0x00" domain="0x0000"
                    function="0x0" slot="0x03" type="pci"/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge=""/>
                <link state="down"/>
                <boot order="1"/>
            </interface>'''
        meta = {'vmid': 'VMID'}
        expected_xml = u'''
            <interface type="bridge">
                <address bus="0x00" domain="0x0000"
                    function="0x0" slot="0x03" type="pci" />
                <mac address="52:54:00:59:F5:3F" />
                <model type="virtio" />
                <source bridge=";vdsmdummy;" />
                <link state="down"/>
                <boot order="1" />
            </interface>'''
        self._check_roundtrip(
            vmdevices.network.Interface,
            interface_xml,
            meta=meta,
            expected_xml=expected_xml
        )

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_interface_vmfex(self):
        interface_xml = u'''
            <interface type='network'>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source network='direct-pool'/>
                <virtualport type='802.1Qbh'>
                    <parameters profileid='OvirtProfileID'/>
                </virtualport>
            </interface>'''
        # the real work is done by the hook, so we check that
        # we correctly initialized what we could
        meta = {'vmid': 'VMID', 'network': 'ovirttest'}
        expected_xml = u'''
            <interface type="network">
                <mac address="52:54:00:59:F5:3F" />
                <model type="virtio" />
                <source bridge="ovirttest" />
                <link state="up" />
            </interface>'''
        self._check_roundtrip(
            vmdevices.network.Interface,
            interface_xml,
            meta=meta,
            expected_xml=expected_xml
        )

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_interface_sriov_only_host_address(self):
        """
        This is what we expect on the very first run. The device has not
        one guest address (managed and assigned by libvirt), just the
        host address to identify the host device.
        """
        interface_xml = u'''
            <interface managed="no" type="hostdev">
                <mac address="ff:ff:ff:ff:ff:ff"/>
                <source>
                    <address bus="0x05" domain="0x0000"
                        function="0x7" slot="0x10" type="pci"/>
                </source>
                <vlan>
                    <tag id="3"/>
                </vlan>
                <link state="up"/>
                <boot order="9"/>
                <driver name="vfio"/>
            </interface>'''
        meta = {'vmid': 'VMID'}
        with MonkeyPatchScope([
            (hostdev, 'libvirtconnection', FakeLibvirtConnection())
        ]):
            self._check_roundtrip(
                vmdevices.network.Interface, interface_xml, meta=meta)

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_interface_sriov_with_host_and_guest_address(self):
        """
        This is what we could get from the second run, and following.
        Engine may or may not pass the guest address, both ways are legal.
        Any way, we should never confuse them.
        """
        interface_xml = u'''
            <interface managed="no" type="hostdev">
                <address bus="0x01" domain="0x0000" function="0x0"
                    slot="0x02" type="pci"/>
                <mac address="ff:ff:ff:ff:ff:ff"/>
                <source>
                    <address bus="0x05" domain="0x0000"
                        function="0x7" slot="0x10" type="pci"/>
                </source>
                <vlan>
                    <tag id="3"/>
                </vlan>
                <link state="up"/>
                <boot order="9"/>
                <driver name="vfio"/>
            </interface>'''
        meta = {'vmid': 'VMID'}
        with MonkeyPatchScope([
            (hostdev, 'libvirtconnection', FakeLibvirtConnection())
        ]):
            self._check_roundtrip(
                vmdevices.network.Interface, interface_xml, meta=meta)

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_interface_hostdev(self):
        interface_xml = u'''
            <interface type='hostdev' managed='no'>
              <address type='pci' domain='0x0000' bus='0x00'
                slot='0x04' function='0x0'/>
              <mac address='00:1a:4a:16:91:df'/>
              <source>
                <address type='pci' domain='0x0000' bus='0x05'
                    slot='0x00' function='0x1'/>
              </source>
              <link state="up"/>
              <driver name='vfio'/>
            </interface>'''
        meta = {'vmid': 'VMID'}
        with MonkeyPatchScope([
            (hostdev.libvirtconnection, 'get', hostdevlib.Connection),
            (vmdevices.hostdevice, 'detach_detachable',
                lambda *args, **kwargs: None),
            (vmdevices.hostdevice, 'reattach_detachable',
                lambda *args, **kwargs: None),
        ]):
            self._check_roundtrip(
                vmdevices.network.Interface, interface_xml, meta=meta)

    @MonkeyPatch(vmdevices.network.net_api, 'net2vlan', lambda x: 101)
    def test_interface_ovs(self):
        proxy = FakeProxy(ovs_bridge={
            'ovn_net_1': {
                'name': 'vdsmbr_fffffff',
                'dpdk_enabled': False
            }
        })

        interface_xml = u'''
            <interface type="bridge">
                <address bus="0x00" domain="0x0000"
                    function="0x0" slot="0x03" type="pci"/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge="ovn_net_1"/>
                <boot order="1"/>
                <driver name="vhost" queues="4"/>
                <tune>
                    <sndbuf>128</sndbuf>
                </tune>
            </interface>'''

        expected_xml = u'''
            <interface type="bridge">
                <address bus="0x00" domain="0x0000"
                    function="0x0" slot="0x03" type="pci"/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge="vdsmbr_fffffff"/>
                <virtualport type="openvswitch" />
                <vlan>
                    <tag id="101" />
                </vlan>
                <link state="up"/>
                <boot order="1"/>
                <driver name="vhost" queues="4"/>
                <tune>
                    <sndbuf>128</sndbuf>
                </tune>
            </interface>'''
        meta = {'vmid': 'VMID'}

        with MonkeyPatchScope([
            (vmdevices.network.supervdsm, 'getProxy', lambda: proxy)
        ]):
            self._check_roundtrip(
                vmdevices.network.Interface,
                interface_xml,
                expected_xml=expected_xml,
                meta=meta
            )

    def test_storage(self):
        with pytest.raises(NotImplementedError):
            vmdevices.storage.Drive.from_xml_tree(
                self.log, None, {}
            )

    @permutations(_STORAGE_TEST_DATA)
    def test_storage_from_xml(self, storage_xml, meta):
        dev = vmdevices.storage.Drive(
            self.log, **vmdevices.storagexml.parse(
                xmlutils.fromstring(storage_xml),
                {} if meta is None else meta
            )
        )
        self._check_device_xml(dev, storage_xml)

    @permutations(_TRANSIENT_STORAGE_TEST_DATA)
    def test_transient_storage_from_xml(self, storage_xml, meta):
        dev = vmdevices.storage.Drive(
            self.log, **vmdevices.storagexml.parse(
                xmlutils.fromstring(storage_xml),
                {} if meta is None else meta
            )
        )
        assert dev.shared == vmdevices.storage.DRIVE_SHARED_TYPE.TRANSIENT

    def test_storage_from_incomplete_xml(self):
        storage_xml = '''<disk device="disk" snapshot="no" type="file">
            <source>
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target bus="virtio" dev="vda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="none" error_policy="stop"
                    io="threads" name="qemu" type="raw"/>
        </disk>'''
        expected_xml = '''<disk device="disk" snapshot="no" type="file">
            <source file="">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target bus="virtio" dev="vda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="none" error_policy="stop"
                    io="threads" name="qemu" type="raw"/>
        </disk>'''
        dev = vmdevices.storage.Drive(
            self.log, **vmdevices.storagexml.parse(
                xmlutils.fromstring(storage_xml),
                {}
            )
        )
        self._check_device_xml(dev, expected_xml)

    def test_cdrom_from_xml_without_driver_element(self):
        # test that we add the 'driver' element with the
        # defaults in in the XML-based initialization flow

        # this is the common XML template.
        cdrom_xml = u'''
            <disk type="file" device="cdrom" snapshot="no">
                <address bus="1" controller="0" unit="0"
                         type="drive" target="0"/>
                <source file="" startupPolicy="optional">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <target dev="hdc" bus="ide"/>
                <readonly/>
                {driver_xml}
            </disk>'''
        # simulate we receive a XML snippet without the driver
        # element. This is unlikely with Engine >= 4.2, but still
        # supported.
        source_xml = cdrom_xml.format(driver_xml='')
        # The output XML must include a "driver" element, built
        # using the defaults. Everything else should be the same
        # (see below for more details).
        expected_xml = cdrom_xml.format(
            driver_xml=u'''<driver name="qemu" type="raw"
            error_policy="stop"/>''')

        dev = vmdevices.storage.Drive(
            self.log, **vmdevices.storagexml.parse(
                xmlutils.fromstring(source_xml),
                {}
            )
        )
        # everything which is not related to the driver element should be
        # derived from the source XML, thus the source and the expected
        # XML snippets should be equal - bar the driver element.
        self._check_device_xml(dev, expected_xml)

    def test_cdrom_from_xml_without_source_element(self):
        cdrom_xml = u'''
          <disk type="file" device="cdrom">
            <address type='drive' controller='0' bus='1' target='0' unit='0'/>
            <target dev='hdc' bus='ide' tray='open'/>
            <readonly/>
            <driver name='qemu' type='raw' error_policy='report'/>
         </disk>'''
        expected_xml = u'''
          <disk type="file" device="cdrom" snapshot="no">
            <address type='drive' controller='0' bus='1' target='0' unit='0'/>
            <source file="" startupPolicy="optional">
              <seclabel model="dac" relabel="no" type="none"/>
            </source>
            <target dev='hdc' bus='ide'/>
            <readonly/>
            <driver name='qemu' type='raw' error_policy='report'/>
         </disk>'''
        dom = xmlutils.fromstring(cdrom_xml)
        dev = vmdevices.storage.Drive(
            self.log, **vmdevices.storagexml.parse(dom, {})
        )
        self._check_device_xml(dev, expected_xml)

    def _check_roundtrip(self, klass, dev_xml, meta=None, expected_xml=None):
        dev = klass.from_xml_tree(
            self.log,
            xmlutils.fromstring(dev_xml),
            {} if meta is None else meta
        )
        self._check_device_attrs(dev)
        self._check_device_xml(dev, dev_xml, expected_xml)

    def _check_device_attrs(self, dev):
        assert hasattr(dev, 'specParams')
        if (isinstance(dev, vmdevices.network.Interface) or
                isinstance(dev, vmdevices.storage.Drive)):
            assert hasattr(dev, 'vm_custom')

    def _check_device_xml(self, dev, dev_xml, expected_xml=None):
        dev.setup()
        try:
            rebuilt_xml = xmlutils.tostring(dev.getXML(), pretty=True)
            # make troubleshooting easier
            print(rebuilt_xml)
            result_xml = dev_xml if expected_xml is None else expected_xml
            self.assertXMLEqual(rebuilt_xml, result_xml)
        finally:
            dev.teardown()


_DRIVE_PAYLOAD_XML = u"""<domain type='kvm' id='2'>
  <uuid>dd493ddc-1ef2-4445-a248-4a7bc266a671</uuid>
  <metadata
        xmlns:ovirt-tune='http://ovirt.org/vm/tune/1.0'
        xmlns:ovirt-vm='http://ovirt.org/vm/1.0'>
    <ovirt-tune:qos/>
    <ovirt-vm:vm>
      <ovirt-vm:device devtype="disk" name="hdd">
        <ovirt-vm:readonly type='bool'>true</ovirt-vm:readonly>
        <ovirt-vm:payload>
        <ovirt-vm:volId>config-1</ovirt-vm:volId>
  <ovirt-vm:file path='openstack/content/0000'>AAA</ovirt-vm:file>
  <ovirt-vm:file path='openstack/latest/meta_data.json'>BBB</ovirt-vm:file>
  <ovirt-vm:file path='openstack/latest/user_data'>CCC</ovirt-vm:file>
        </ovirt-vm:payload>
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
  <devices>
    <emulator>/usr/libexec/qemu-kvm</emulator>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source startupPolicy='optional'/>
      <target dev='hdd' bus='ide'/>
      <readonly/>
      <driver error_policy="report" name="qemu" type="raw" />
    </disk>
  </devices>
</domain>"""


_INVALID_DEVICE_XML = u"""<domain type='kvm' id='2'>
  <uuid>1234</uuid>
  <devices>
    <graphics/>
  </devices>
</domain>
"""


class DeviceFromXMLTests(XMLTestCase):

    def test_payload_from_metadata(self):
        vmPayload = {
            'volId': 'config-1',
            'file': {
                'openstack/content/0000': 'AAA',
                'openstack/latest/meta_data.json': 'BBB',
                'openstack/latest/user_data': 'CCC',
            }
        }

        md_desc = metadata.Descriptor.from_xml(_DRIVE_PAYLOAD_XML)
        root = xmlutils.fromstring(_DRIVE_PAYLOAD_XML)

        dev_xml = root.find('./devices/disk')

        with md_desc.device(devtype='disk', name='hdd') as meta:
            dev_obj = vmdevices.storage.Drive(
                self.log, **vmdevices.storagexml.parse(dev_xml, meta)
            )
            assert dev_obj.specParams['vmPayload'] == vmPayload

    def test_payload_from_metadata_dump(self):
        expected_xml = u'''<ovirt-vm:vm xmlns:ovirt-vm='http://ovirt.org/vm/1.0'>
  <ovirt-vm:device devtype="disk" name="hdd">
    <ovirt-vm:readonly type='bool'>True</ovirt-vm:readonly>
    <ovirt-vm:payload>
      <ovirt-vm:volId>config-1</ovirt-vm:volId>
      <ovirt-vm:file path='openstack/content/0000'>AAA</ovirt-vm:file>
      <ovirt-vm:file path='openstack/latest/meta_data.json'>BBB</ovirt-vm:file>
      <ovirt-vm:file path='openstack/latest/user_data'>CCC</ovirt-vm:file>
    </ovirt-vm:payload>
  </ovirt-vm:device>
</ovirt-vm:vm>'''

        md_desc = metadata.Descriptor.from_xml(_DRIVE_PAYLOAD_XML)
        self.assertXMLEqual(md_desc.to_xml(), expected_xml)

    def test_device_core_attributes_present_and_never_none(self):
        he_xml = read_data('hostedengine.xml')
        dom_desc = DomainDescriptor(he_xml)
        md_desc = metadata.Descriptor.from_xml(he_xml)
        dev_objs = vmdevices.common.dev_map_from_domain_xml(
            'HE', dom_desc, md_desc, self.log
        )
        for devices in dev_objs.values():
            for dev in devices:
                print(dev)  # debug aid
                assert dev.type is not None
                assert dev.device is not None

    def test_erroneous_device_init(self):
        dom_desc = DomainDescriptor(_INVALID_DEVICE_XML)
        for dom in dom_desc.get_device_elements('graphics'):
            dev = vmdevices.graphics.Graphics(dom, '1234')
            with pytest.raises(vmxml.NotFound):
                dev._display_network()


# invalid domain with only the relevant sections added
# UUID has no meaning, randomly generated
_DOMAIN_MD_MATCH_XML = u"""<domain type='kvm' id='2'>
  <uuid>dd493ddc-1ef2-4445-a248-4a7bc266a671</uuid>
  <metadata
        xmlns:ovirt-tune='http://ovirt.org/vm/tune/1.0'
        xmlns:ovirt-vm='http://ovirt.org/vm/1.0'>
    <ovirt-tune:qos/>
    <ovirt-vm:vm>
      <ovirt-vm:device devtype="disk" name="sda">
        <ovirt-vm:RBD>/dev/rbd/pool/volume-uuid</ovirt-vm:RBD>
      </ovirt-vm:device>
      <ovirt-vm:device mac_address="00:1a:4a:16:01:00">
        <ovirt-vm:portMirroring>
          <ovirt-vm:network>network1</ovirt-vm:network>
          <ovirt-vm:network>network2</ovirt-vm:network>
        </ovirt-vm:portMirroring>
      </ovirt-vm:device>
      <ovirt-vm:device alias='net0'>
        <ovirt-vm:network>ovirtmgmt0</ovirt-vm:network>
      </ovirt-vm:device>
      <ovirt-vm:device alias='net1' mac_address='00:1a:3b:16:10:16'>
        <ovirt-vm:network>ovirtmgmt1</ovirt-vm:network>
      </ovirt-vm:device>
      <ovirt-vm:device mac_address='00:1a:55:ff:20:26'>
        <ovirt-vm:network>ovirtmgmt2</ovirt-vm:network>
      </ovirt-vm:device>
      <ovirt-vm:device mac_address='00:1a:55:ff:30:36'>
        <ovirt-vm:network>ovirtmgmt2</ovirt-vm:network>
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
  <devices>
    <emulator>/usr/libexec/qemu-kvm</emulator>
    <disk type='block' device='disk' snapshot='no'>
        <driver name='qemu' type='raw' cache='none'/>
        <source dev='/dev/rbd/pool/volume-uuid'>
        <seclabel model='dac' relabel='no'/>
        </source>
        <backingStore/>
        <target dev='sda' bus='scsi'/>
        <serial>44ab108a-62e6-480e-b44c-aac301227f94</serial>
        <boot order='1'/>
        <alias name='ua-44ab108a-62e6-480e-b44c-aac301227f94'/>
        <address type='drive' controller='0' bus='0' target='0' unit='0'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source startupPolicy='optional'/>
      <backingStore/>
      <target dev='hdc' bus='ide'/>
      <readonly/>
      <driver error_policy="report" name="qemu" type="raw" />
    </disk>
    <controller type='virtio-serial' index='0' ports='16'>
      <alias name='virtio-serial0'/>
    </controller>
    <controller type='usb' index='0'>
      <alias name='usb'/>
    </controller>
    <controller type='pci' index='0' model='pci-root' />
    <interface type='bridge'>
      <mac address='00:1a:4a:16:01:51'/>
      <source bridge='INVALID0'/>
      <target dev='vnet0'/>
      <model type='virtio'/>
      <filterref filter='vdsm-no-mac-spoofing'/>
      <link state='up'/>
      <boot order='2'/>
      <alias name='net0'/>
    </interface>
    <interface type='bridge'>
      <mac address='00:1a:3b:16:10:16'/>
      <source bridge='INVALID1'/>
      <target dev='vnet0'/>
      <model type='virtio'/>
      <filterref filter='vdsm-no-mac-spoofing'/>
      <link state='up'/>
      <boot order='2'/>
      <alias name='net1'/>
    </interface>
    <interface type='bridge'>
      <mac address='00:1a:55:ff:20:26'/>
      <source bridge='INVALID1'/>
      <target dev='vnet0'/>
      <model type='virtio'/>
      <filterref filter='vdsm-no-mac-spoofing'/>
      <link state='up'/>
      <boot order='2'/>
    </interface>
    <interface type='bridge'>
      <mac address='00:1a:4a:16:01:00'/>
      <source bridge='network1'/>
      <target dev='vnet1'/>
      <model type='virtio'/>
      <filterref filter='vdsm-no-mac-spoofing'/>
      <link state='up'/>
    </interface>
    <interface type='bridge'>
      <mac address='00:1a:55:ff:30:36'/>
      <source bridge='network4'/>
      <target dev='vnet4'/>
      <model type='virtio'/>
      <link state='up'/>
    </interface>
  </devices>
</domain>"""


class DeviceMetadataMatchTests(XMLTestCase):

    def setUp(self):
        self.dom_desc = DomainDescriptor(_DOMAIN_MD_MATCH_XML)
        self.md_desc = metadata.Descriptor.from_xml(_DOMAIN_MD_MATCH_XML)

    def test_match_interface_by_alias_only_fails(self):
        # fails because we
        # assert set(matching_attrs) in set(device_metadata_attrs)
        # while the reverse can be false with no consequences.
        dev_objs = vmdevices.common.dev_map_from_domain_xml(
            'TESTING', self.dom_desc, self.md_desc, self.log
        )
        nic = self._find_nic_by_mac(dev_objs, '00:1a:4a:16:01:51')
        assert nic.network == 'INVALID0'

    def test_match_interface_by_mac_only_succeeds(self):
        dev_objs = vmdevices.common.dev_map_from_domain_xml(
            'TESTING', self.dom_desc, self.md_desc, self.log
        )
        nic = self._find_nic_by_mac(dev_objs, '00:1a:3b:16:10:16')
        assert nic.network == 'ovirtmgmt1'

    def test_match_interface_by_mac_and_alias_succeeds(self):
        # mac is enough, but we match extra arguments if given
        dev_objs = vmdevices.common.dev_map_from_domain_xml(
            'TESTING', self.dom_desc, self.md_desc, self.log
        )
        nic = self._find_nic_by_mac(dev_objs, '00:1a:55:ff:20:26')
        assert nic.network == 'ovirtmgmt2'

    def test_port_mirroring(self):
        dev_objs = vmdevices.common.dev_map_from_domain_xml(
            'TESTING', self.dom_desc, self.md_desc, self.log
        )
        # random MAC, any nic with portMirroring configured is fine
        nic1 = self._find_nic_by_mac(dev_objs, '00:1a:55:ff:20:26')
        assert nic1.portMirroring == []

        nic2 = self._find_nic_by_mac(dev_objs, '00:1a:4a:16:01:00')
        assert nic2.portMirroring == ['network1', 'network2']

    def test_attributes_present(self):
        dev_objs = vmdevices.common.dev_map_from_domain_xml(
            'TESTING', self.dom_desc, self.md_desc, self.log
        )
        nic = self._find_nic_by_mac(dev_objs, '00:1a:55:ff:30:36')
        assert nic.filterParameters == []
        assert nic.portMirroring == []
        assert nic.vm_custom == {}

    def _find_nic_by_mac(self, dev_objs, mac_addr):
        for nic in dev_objs[vmdevices.hwclass.NIC]:
            if nic.macAddr == mac_addr:
                return nic
        raise AssertionError('no nic with mac=%s found' % mac_addr)

    def test_correct_rbd_disk_metadata(self):
        drives = vmdevices.common.storage_device_params_from_domain_xml(
            'TESTING', self.dom_desc, self.md_desc, self.log
        )

        disk_objs = [
            vmdevices.storage.Drive(self.log, **params)
            for params in drives
        ]

        rbd_drive = lookup.drive_by_name(disk_objs, 'sda')

        assert getattr(rbd_drive, 'RBD') == '/dev/rbd/pool/volume-uuid'


_VM_MDEV_XML = """<?xml version='1.0' encoding='utf-8'?>
<domain xmlns:ns0="http://ovirt.org/vm/tune/1.0"
        xmlns:ovirt-vm="http://ovirt.org/vm/1.0" type="kvm">
  <name>vm</name>
  <uuid>6a28e9f6-6627-49b8-8c24-741ab810ecc0</uuid>
  <devices>
    <hostdev mode="subsystem" model="vfio-pci" type="mdev">
      <source>
        <address uuid="c1f343ae-99a5-4d82-9d5c-203cd4b7dac0" />
      </source>
    </hostdev>
  </devices>
  <metadata>
    <ovirt-vm:vm>
      <clusterVersion>4.2</clusterVersion>
      <ovirt-vm:device devtype="hostdev"
                       uuid="c1f343ae-99a5-4d82-9d5c-203cd4b7dac0">
        <ovirt-vm:mdevType>graphics-card-1%(placement)s</ovirt-vm:mdevType>
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
</domain>
"""


class FakeLibvirtConnection(object):

    def get(self, *args, **kwargs):
        return fake.Connection()
