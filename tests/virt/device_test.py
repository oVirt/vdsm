#
# Copyright 2008-2020 Red Hat, Inc.
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

import os.path

from vdsm.common import hostdev
from vdsm.common import response
from vdsm.common import xmlutils
from vdsm import constants
import vdsm
import vdsm.virt
from vdsm.virt import utils
from vdsm.virt import vmdevices
from vdsm.virt import vmxml
from vdsm.virt.domain_descriptor import DomainDescriptor
from vdsm.virt.vmdevices import graphics
from vdsm.virt.vmdevices import hwclass

from monkeypatch import MonkeyPatch, MonkeyPatchScope
from testlib import permutations, expandPermutations, make_config, read_data
from testlib import VdsmTestCase as TestCaseBase
from testlib import XMLTestCase

from . import vmfakelib as fake


@expandPermutations
class TestVmDevices(XMLTestCase):

    PCI_ADDR = \
        'bus="0x00" domain="0x0000" function="0x0" slot="0x03" type="pci"'
    PCI_ADDR_DICT = {'slot': '0x03', 'bus': '0x00', 'domain': '0x0000',
                     'function': '0x0', 'type': 'pci'}

    GRAPHICS_NO_DISPLAY_NETWORK = """
        <graphics autoport="yes" passwd="xxx"
                  passwdValidTo="1970-01-01T00:00:01"
                  port="-1" tlsPort="-1" type="spice">
          <channel mode="secure" name="main"/>
          <listen type="address" address="1.2.3.4"/>
        </graphics>
    """
    GRAPHICS_DISPLAY_NETWORK = """
        <graphics autoport="yes" passwd="xxx"
                  passwdValidTo="1970-01-01T00:00:01"
                  port="5900" tlsPort="5901" type="spice">
          <channel mode="secure" name="main"/>
          <listen network="vdsm-ovirtmgmt" type="network"
                  address="1.2.3.4"/>
        </graphics>
    """

    def setUp(self):
        self.conf = {
            'vmName': 'testVm',
            'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
            'smp': '8', 'maxVCpus': '160',
            'memSize': '1024', 'memGuaranteedSize': '512',
        }

        self.vnc_graphics = (
            '''
            <graphics type="vnc">
              <listen type="network" network="ovirtmgmt"/>
            </graphics>''',
            '''
            <graphics type="spice" port="-1" keyMap="en-us">
              <listen type="network" network="vmDisplay"/>
            </graphics>
''',)

        self.spice_graphics = (
            '''
            <graphics type="spice">
              <listen type="network" network="ovirtmgmt"/>
            </graphics>''',
            '''
            <graphics type="spice" port="-1" tlsPort="-1"
                      spiceSecureChannels="sfoo,sbar">
              <listen type="network" network="ovirtmgmt"/>
            </graphics>
''',)

        self.graphics_devices = (self.vnc_graphics +
                                 self.spice_graphics)

    def testGraphicDeviceHeadlessSupported(self):
        conf = {}
        conf.update(self.conf)
        assert vmdevices.graphics.isSupportedDisplayType(conf)

    def testHasSpiceEngineXML(self):
        conf = {}
        conf.update(self.conf)
        conf['xml'] = read_data('domain.xml')
        with fake.VM(conf) as testvm:
            assert testvm.hasSpice

    def testInterfaceXMLBandwidthUpdate(self):
        originalBwidthXML = """
                <bandwidth>
                    <inbound average="1000" burst="1024" peak="5000"/>
                    <outbound average="128" burst="256"/>
                </bandwidth>"""
        NEW_OUT = {'outbound': {'average': 1042, 'burst': 128, 'peak': 500}}
        updatedBwidthXML = """
                <bandwidth>
                    <inbound average="1000" burst="1024" peak="5000"/>
                    <outbound average="%(average)s" burst="%(burst)s"
                    peak="%(peak)s"/>
                </bandwidth>""" % NEW_OUT['outbound']

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
        orig_bandwidth = iface.getXML().findall('bandwidth')[0]
        self.assert_dom_xml_equal(orig_bandwidth, originalBwidthXML)
        bandwith = iface.get_bandwidth_xml(NEW_OUT, orig_bandwidth)
        self.assert_dom_xml_equal(bandwith, updatedBwidthXML)

    def testInterfaceFilterUpdate(self):
        originalFilterXML = """
                <filterref filter='vdsm-no-mac-spoofing'/>"""
        NEW_OUT = {'filter': {'name': 'IP', 'value': '127.0.0.1'}}
        updatedFilterXML = """
                <filterref filter='clean-traffic'>
                    <parameter name='%(name)s' value='%(value)s'/>
                </filterref>""" % NEW_OUT['filter']

        dev = {'nicModel': 'virtio', 'macAddr': '52:54:00:59:F5:3F',
               'network': 'ovirtmgmt', 'address': self.PCI_ADDR_DICT,
               'device': 'bridge', 'type': 'interface',
               'bootOrder': '1', 'filter': 'vdsm-no-mac-spoofing',
               'specParams': {'inbound': {'average': 1000, 'peak': 5000,
                                          'burst': 1024},
                              'outbound': {'average': 128, 'burst': 256}},
               'custom': {'queues': '7'},
               'vm_custom': {'vhost': 'ovirtmgmt:true', 'sndbuf': '0'},
               }
        iface = vmdevices.network.Interface(self.log, **dev)
        ifaceXML = iface.getXML()
        orig_filterref = ifaceXML.findall('filterref')[0]
        self.assert_dom_xml_equal(orig_filterref, originalFilterXML)
        vmdevices.network.update_filterref_xml(
            ifaceXML,
            "clean-traffic",
            [{"name": "IP",
              "value": "127.0.0.1"}])
        filter = ifaceXML.findall('filterref')[0]
        self.assert_dom_xml_equal(filter, updatedFilterXML)

    def test_interface_update(self):
        devices = '''
            <interface type="bridge">
              <mac address="52:54:00:59:F5:3F"/>
              <model type="virtio"/>
              <source bridge="ovirtmgmt"/>
              <link state="up"/>
              <alias name="ua-net1"/>
              <target dev="net1"/>
              <bandwidth>
                 <inbound average="1000" peak="5000" burst="1024"/>
                 <inbound average="128" burst="256"/>
              </bandwidth>
            </interface>
        '''
        params = {'linkActive': 'true', 'alias': 'ua-net1', 'name': 'net1',
                  'deviceType': 'interface', 'network': 'ovirtmgmt2',
                  'specParams': {'inbound': {}, 'outbound': {}}}
        updated_xml = '''
            <interface type="bridge">
              <mac address="52:54:00:59:F5:3F"/>
              <model type="virtio"/>
              <source bridge="ovirtmgmt2"/>
              <link state="up"/>
              <alias name="ua-net1"/>
              <bandwidth/>
            </interface>
        '''
        with fake.VM(xmldevices=devices, create_device_objects=True) as testvm:
            testvm._dom = fake.Domain()
            res = testvm.updateDevice(params)
            assert 'vmList' in res
            self.assertXMLEqual(testvm._dom.devXml, updated_xml)

    def testUpdateDriverInSriovInterface(self):
        interface_xml = """<?xml version="1.0" encoding="utf-8"?>
        <domain type="kvm"
          xmlns:qemu="http://libvirt.org/schemas/domain/qemu/1.0">
          <devices>
            <interface type='hostdev' managed='no'>
              <source>
               <address type='pci' domain='0x0000' bus='0x00' slot='0x07'
               function='0x0'/>
              </source>
              <driver name='vfio' queues='10'/>
              <mac address='ff:ff:ff:ff:ff:ff'/>
              <vlan>
                <tag id='3'/>
              </vlan>
              <boot order='9'/>
            </interface>
          </devices>
        </domain>"""
        with fake.VM() as testvm:
            interface_conf = {
                'type': hwclass.NIC, 'device': 'hostdev',
                'hostdev': 'pci_0000_05_00_1', 'macAddr': 'ff:ff:ff:ff:ff:ff',
                'specParams': {'vlanid': 3}, 'bootOrder': '9'}
            interface_dev = vmdevices.network.Interface(
                testvm.log, **interface_conf)

            testvm.conf['devices'] = [interface_conf]
            device_conf = [interface_dev]
            testvm._domain = DomainDescriptor(interface_xml)

            vmdevices.network.Interface.update_device_info(
                testvm, device_conf)

            assert interface_dev.driver == \
                {'queues': '10', 'name': 'vfio'}

    def test_interface_update_disappear_queues(self):
        interface_xml = """<interface type="bridge">
          <model type="virtio" />
          <link state="up" />
          <source bridge="ovirtmgmt" />
          <driver name="vhost" queues="1" />
          <alias name="ua-604c7957-9aaf-4e86-bcaa-87e12571449b" />
          <mac address="00:1a:4a:16:01:50" />
          <mtu size="1500" />
          <filterref filter="vdsm-no-mac-spoofing" />
          <bandwidth />
        </interface>
        """
        updated_xml = """<?xml version="1.0" encoding="utf-8"?>
        <domain type="kvm"
          xmlns:qemu="http://libvirt.org/schemas/domain/qemu/1.0">
          <devices>
            <interface type='bridge'>
              <mac address='00:1a:4a:16:01:50'/>
              <source bridge='ovirtmgmt'/>
              <target dev='vnet0'/>
              <model type='virtio'/>
              <driver name='vhost'/>
              <filterref filter='vdsm-no-mac-spoofing'/>
              <link state='up'/>
              <mtu size='1500'/>
              <alias name='ua-604c7957-9aaf-4e86-bcaa-87e12571449b'/>
              <address type='pci' domain='0x0000'
                       bus='0x00' slot='0x03' function='0x0'/>
            </interface>
          </devices>
        </domain>"""
        meta = {'vmid': 'VMID'}  # noone cares about the actual ID
        with fake.VM() as testvm:
            nic = vmdevices.network.Interface.from_xml_tree(
                self.log, xmlutils.fromstring(interface_xml), meta=meta
            )
            saved_driver = nic.driver.copy()
            testvm._devices[hwclass.NIC].append(nic)
            testvm._domain = DomainDescriptor(updated_xml)

            vmdevices.network.Interface.update_device_info(
                testvm, testvm._devices[hwclass.NIC]
            )

            assert nic.driver == saved_driver

    def test_mdev_details_(self):
        details = hostdev._mdev_type_details('graphics-card-1', '/nonexistent')
        for f in hostdev._MDEV_FIELDS:
            assert getattr(details, f) == \
                ('graphics-card-1' if f == 'name' else '')

    def test_graphics_no_display_network(self):
        dom = xmlutils.fromstring(self.GRAPHICS_NO_DISPLAY_NETWORK)
        device = vmdevices.graphics.Graphics(dom, 'vmid')
        assert device._display_network() is None

    def test_graphics_display_network(self):
        dom = xmlutils.fromstring(self.GRAPHICS_DISPLAY_NETWORK)
        device = vmdevices.graphics.Graphics(dom, '1234')
        assert device._display_network() == 'ovirtmgmt'

    def test_display_info_no_display_network(self):
        xml = ('<domain><devices>%s</devices></domain>' %
               (self.GRAPHICS_NO_DISPLAY_NETWORK,))
        domain = DomainDescriptor(xml)
        info = vmdevices.graphics.display_info(domain)
        assert info == [{'type': 'spice',
                                 'port': '-1',
                                 'tlsPort': '-1',
                                 'ipAddress': '1.2.3.4'}]

    def test_display_info_display_network(self):
        xml = ('<domain><devices>%s</devices></domain>' %
               (self.GRAPHICS_DISPLAY_NETWORK,))
        domain = DomainDescriptor(xml)
        info = vmdevices.graphics.display_info(domain)
        assert info == [{'type': 'spice',
                                 'port': '5900',
                                 'tlsPort': '5901',
                                 'ipAddress': '1.2.3.4'}]


class ConsoleTests(TestCaseBase):

    def setUp(self):
        self.cfg = {
            'vmName': 'testVm',
            'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f'
        }
        self._cleaned_path = None
        self._expected_path = os.path.join(
            constants.P_OVIRT_VMCONSOLES,
            '%s.sock' % self.cfg['vmId'])

    def test_console_pty_not_prepare_path(self):
        supervdsm = fake.SuperVdsm()
        with MonkeyPatchScope([(vmdevices.core, 'supervdsm', supervdsm)]):
            dom = xmlutils.fromstring("""
        <console type="pty">
            <source path="/abc/def"/>
            <target port="0" type="serial"/>
            <alias name="ua-1234"/>
        </console>
""")
            vmdevices.core.prepare_console(dom, self.cfg['vmId'])
            assert supervdsm.prepared_path is None

    def test_console_usock_prepare_path(self):
        supervdsm = fake.SuperVdsm()
        with MonkeyPatchScope([(vmdevices.core, 'supervdsm', supervdsm)]):
            dom = xmlutils.fromstring("""
        <console type="unix">
            <source mode="bind" path="%s"/>
            <target port="0" type="serial"/>
            <alias name="ua-1234"/>
        </console>
""" % (self._expected_path,))
            vmdevices.core.prepare_console(dom, self.cfg['vmId'])
            assert supervdsm.prepared_path == \
                self._expected_path
            assert supervdsm.prepared_path_group == \
                constants.OVIRT_VMCONSOLE_GROUP

    def test_console_pty_not_cleanup_path(self):
        def _fake_cleanup(path):
            self._cleaned_path = path

        with MonkeyPatchScope([(vmdevices.core,
                                'cleanup_guest_socket', _fake_cleanup)]):
            dom = xmlutils.fromstring("""
        <console type="pty">
            <source path="/abc/def"/>
            <target port="0" type="serial"/>
            <alias name="ua-1234"/>
        </console>
""")
            vmdevices.core.cleanup_console(dom, self.cfg['vmId'])
            assert self._cleaned_path is None

    def test_console_usock_cleanup_path(self):
        def _fake_cleanup(path):
            self._cleaned_path = path

        with MonkeyPatchScope([(vmdevices.core,
                                'cleanup_guest_socket', _fake_cleanup)]):

            dom = xmlutils.fromstring("""
        <console type="unix">
            <source mode="bind" path="%s"/>
            <target port="0" type="serial"/>
            <alias name="ua-1234"/>
        </console>
""" % (self._expected_path,))
            vmdevices.core.cleanup_console(dom, self.cfg['vmId'])
            assert self._cleaned_path == self._expected_path


class BrokenSuperVdsm(fake.SuperVdsm):

    def setPortMirroring(self, network, nic_name):
        if self.mirrored_networks:
            raise Exception("Too many networks")
        super(BrokenSuperVdsm, self).setPortMirroring(network, nic_name)


@expandPermutations
class TestHotplug(TestCaseBase):

    SD_ID = "1111"
    VOLUME_ID = "3333"

    NIC_HOTPLUG = '''<?xml version='1.0' encoding='UTF-8'?>
<hotplug>
  <devices>
    <interface type="bridge">
      <alias name="ua-nic-hotplugged"/>
      <mac address="66:55:44:33:22:11"/>
      <model type="virtio" />
      <source bridge="ovirtmgmt" />
      <filterref filter="vdsm-no-mac-spoofing" />
      <link state="up" />
      <bandwidth />
    </interface>
  </devices>
  <metadata xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
    <ovirt-vm:vm>
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
'''
    DISK_HOTPLUG = '''<?xml version='1.0' encoding='UTF-8'?>
<hotplug>
  <devices>
    <disk type='file' device='disk' snapshot='no'>
      <driver name='qemu' type='raw' cache='none' error_policy='stop'
              io='threads'/>
      <source file='/path/to/file'/>
      <backingStore/>
      <target dev='sda' bus='scsi'/>
      <serial>1234</serial>
      <boot order='1'/>
      <address type='drive' controller='0' bus='0' target='0' unit='0'/>
    </disk>
  </devices>
  <metadata xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
    <ovirt-vm:vm>
      <ovirt-vm:device devtype="disk" name="sda">
        <ovirt-vm:domainID>%(sd_id)s</ovirt-vm:domainID>
        <ovirt-vm:imageID>1234</ovirt-vm:imageID>
        <ovirt-vm:poolID>2222</ovirt-vm:poolID>
        <ovirt-vm:volumeID>%(volume_id)s</ovirt-vm:volumeID>
        <ovirt-vm:volumeChain>
            <ovirt-vm:volumeChainNode>
              <ovirt-vm:domainID>%(sd_id)s</ovirt-vm:domainID>
              <ovirt-vm:imageID>1234</ovirt-vm:imageID>
              <ovirt-vm:leaseOffset type="int">0</ovirt-vm:leaseOffset>
              <ovirt-vm:leasePath>/path/to.lease</ovirt-vm:leasePath>
              <ovirt-vm:path>/path/to/disk</ovirt-vm:path>
              <ovirt-vm:volumeID>%(volume_id)s</ovirt-vm:volumeID>
            </ovirt-vm:volumeChainNode>
        </ovirt-vm:volumeChain>
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
</hotplug>
''' % {'sd_id': SD_ID,
       'volume_id': VOLUME_ID}

    def setUp(self):
        devices = '''
            <interface type="bridge">
              <mac address="11:22:33:44:55:66"/>
              <model type="virtio"/>
              <source bridge="ovirtmgmt"/>
              <link state="up"/>
              <alias name="net2"/>
              <target dev="net2"/>
            </interface>
        '''
        with fake.VM(xmldevices=devices, create_device_objects=True) as vm:
            vm._dom = fake.Domain(vm=vm)
            vm.cif.irs.prepared_volumes = {
                (self.SD_ID, self.VOLUME_ID) : {
                    'truesize': 1024,
                    'apparentsize': 1024,
                }
            }
            self.vm = vm
        self.supervdsm = fake.SuperVdsm()

    def test_disk_hotplug(self):
        vm = self.vm
        params = {'xml': self.DISK_HOTPLUG}
        vm.hotplugDisk(params)
        assert len(vm.getDiskDevices()) == 1
        dev = vm._devices[hwclass.DISK][0]
        assert dev.serial == '1234'
        assert dev.domainID == '1111'
        assert dev.name == 'sda'

    def test_disk_hotunplug(self):
        vm = self.vm
        params = {'xml': self.DISK_HOTPLUG}
        vm.hotunplugDisk(params)
        assert len(vm.getDiskDevices()) == 0

    def test_nic_hotplug(self):
        vm = self.vm
        assert len(vm._devices[hwclass.NIC]) == 1
        params = {'xml': self.NIC_HOTPLUG}
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm.hotplugNic(params)
        assert len(vm._devices[hwclass.NIC]) == 2
        for dev in vm._devices[hwclass.NIC]:
            if dev.macAddr == "66:55:44:33:22:11":
                break
        else:
            raise Exception("Hot plugged device not found")
        assert dev.macAddr == "66:55:44:33:22:11"
        assert dev.network == "test"
        # TODO: Make sure metadata of the original device is initialized in the
        # fake VM.
        # with vm._md_desc.device(mac_address="11:22:33:44:55:66") as dev:
        #     self.assertEqual(dev['network'], "ovirtmgmt")
        with vm._md_desc.device(mac_address="66:55:44:33:22:11") as dev:
            assert dev['network'] == "test"
        assert self.supervdsm.mirrored_networks == \
            [('network1', '',), ('network2', '',)]

    def test_nic_hotplug_mirroring_failure(self):
        vm = self.vm
        supervdsm = BrokenSuperVdsm()
        assert len(vm._devices[hwclass.NIC]) == 1
        params = {'xml': self.NIC_HOTPLUG}
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                supervdsm.getProxy)]):
            vm.hotplugNic(params)
        assert len(vm._devices[hwclass.NIC]) == 1
        dev = vm._devices[hwclass.NIC][0]
        assert dev.macAddr == "11:22:33:44:55:66"
        assert dev.network == "ovirtmgmt"
        # TODO: Make sure metadata of the original device is initialized in the
        # fake VM.
        # with vm._md_desc.device(mac_address="11:22:33:44:55:66") as dev:
        #     self.assertEqual(dev['network'], "ovirtmgmt")
        with vm._md_desc.device(dev_type=hwclass.NIC,
                                mac_address="66:55:44:33:22:11") as dev:
            assert 'network' not in dev
        assert supervdsm.mirrored_networks == []

    def test_nic_hotunplug(self):
        vm = self.vm
        self.test_nic_hotplug()
        assert len(vm._devices[hwclass.NIC]) == 2
        params = {'xml': self.NIC_HOTPLUG}
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm.hotunplugNic(params)
        assert len(vm._devices[hwclass.NIC]) == 1
        dev = vm._devices[hwclass.NIC][0]
        assert dev.macAddr == "11:22:33:44:55:66"
        assert dev.network == "ovirtmgmt"
        # TODO: Make sure metadata of the original device is initialized in the
        # fake VM.
        # with vm._md_desc.device(mac_address="11:22:33:44:55:66") as dev:
        #     self.assertEqual(dev['network'], "ovirtmgmt")
        with vm._md_desc.device(dev_type=hwclass.NIC,
                                mac_addres="66:55:44:33:22:11") as dev:
            assert 'network' not in dev
        assert self.supervdsm.mirrored_networks == []

    def test_delayed_nic_hotunplug(self):
        vm = self.vm
        self.test_nic_hotplug()
        assert len(vm._devices[hwclass.NIC]) == 2
        params = {'xml': self.NIC_HOTPLUG}
        with MonkeyPatchScope([
                (vdsm.common.supervdsm, 'getProxy', self.supervdsm.getProxy),
                (vdsm.virt.vm, 'config',
                 make_config([('vars', 'hotunplug_timeout', '0'),
                              ('vars', 'hotunplug_check_interval', '0.01')])),
        ]):
            self.vm._dom.vm = None
            assert response.is_error(vm.hotunplugNic(params))
            self.vm.onDeviceRemoved('ua-nic-hotplugged')
        assert len(vm._devices[hwclass.NIC]) == 1

    def test_nic_hotunplug_timeout(self):
        vm = self.vm
        self.test_nic_hotplug()
        assert len(vm._devices[hwclass.NIC]) == 2
        params = {'xml': self.NIC_HOTPLUG}
        with MonkeyPatchScope([
                (vdsm.common.supervdsm, 'getProxy', self.supervdsm.getProxy),
                (vdsm.virt.vm, 'config',
                 make_config([('vars', 'hotunplug_timeout', '0'),
                              ('vars', 'hotunplug_check_interval', '0.01')])),
        ]):
            self.vm._dom.vm = None
            assert response.is_error(vm.hotunplugNic(params))
        assert len(vm._devices[hwclass.NIC]) == 2


@expandPermutations
class TestUpdateDevice(TestCaseBase):

    NIC_UPDATE = '''<?xml version='1.0' encoding='UTF-8'?>
<hotplug>
  <devices>
    <interface type="bridge">
      <mac address="11:22:33:44:55:66"/>
      <model type="virtio" />
      <source bridge="ovirtmgmt" />
      <filterref filter="vdsm-no-mac-spoofing" />
      <link state="up" />
      <bandwidth />
      <alias name="net1" />
      {new_node}
    </interface>
  </devices>
  <metadata xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
    <ovirt-vm:vm>
      <ovirt-vm:device mac_address='11:22:33:44:55:66'>
        <ovirt-vm:network>test</ovirt-vm:network>
        <ovirt-vm:portMirroring>
          <ovirt-vm:network>network1</ovirt-vm:network>
          <ovirt-vm:network>network2</ovirt-vm:network>
        </ovirt-vm:portMirroring>
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
</hotplug>
'''

    def setUp(self):
        devices = '''
            <interface type="bridge">
              <mac address="11:22:33:44:55:66"/>
              <model type="virtio"/>
              <source bridge="ovirtmgmt"/>
              <link state="down"/>
              <alias name="net1"/>
              <target dev="net1"/>
            </interface>
        '''
        with fake.VM(xmldevices=devices, create_device_objects=True) as vm:
            vm._dom = fake.Domain(vm=vm)
            self.vm = vm
        self.supervdsm = fake.SuperVdsm()

    @permutations([
        # mtu_old, mtu_new
        (None, None),
        (1492, 1492),
    ])
    def test_nic_update_mtu(self, mtu_old, mtu_new):
        vm = self.vm
        assert len(vm._devices[hwclass.NIC]) == 1
        vm._devices[hwclass.NIC][0].mtu = mtu_old
        mtu = ''
        if mtu_new is not None:
            mtu = '<mtu size="%d" />' % mtu_new
        params = {
            'deviceType': 'interface',
            'xml': self.NIC_UPDATE.format(new_node=mtu),
        }
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm.updateDevice(params)
        assert len(vm._devices[hwclass.NIC]) == 1
        for dev in vm._devices[hwclass.NIC]:
            if dev.macAddr == "11:22:33:44:55:66":
                break
        else:
            raise Exception("Hot plugged device not found")
        assert dev.linkActive
        assert dev.network == 'test'
        assert sorted(dev.portMirroring) == \
            sorted(['network1', 'network2'])
        assert dev.mtu == mtu_new

    @permutations([
        # port_isolated_old, port_isolated_new
        (None, None),
        ("no", "yes"),
        ("yes", "no"),
        ("yes", "yes"),
        (None, "yes"),
        ("yes", None),
        ("yes", "yes"),
    ])
    def test_nic_update_port_isolated(self, port_isolated_old,
                                      port_isolated_new):
        vm = self.vm
        assert len(vm._devices[hwclass.NIC]) == 1
        vm._devices[hwclass.NIC][0].port_isolated = port_isolated_old
        port = ''
        if port_isolated_new is not None:
            port = '<port isolated="%s" />' % port_isolated_new
        params = {
            'deviceType': 'interface',
            'xml': self.NIC_UPDATE.format(new_node=port),
        }
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm.updateDevice(params)
        assert len(vm._devices[hwclass.NIC]) == 1
        for dev in vm._devices[hwclass.NIC]:
            if dev.macAddr == "11:22:33:44:55:66":
                break
        else:
            raise Exception("Hot plugged device not found")
        assert dev.port_isolated == port_isolated_new


class TestRestorePaths(TestCaseBase):

    XML = '''<?xml version='1.0' encoding='UTF-8'?>
    <domain xmlns:ns0="http://ovirt.org/vm/tune/1.0"
            xmlns:ovirt-vm="http://ovirt.org/vm/1.0" type="kvm">
    <name>test</name>
    <uuid>1111</uuid>
    <memory>1310720</memory>
    <currentMemory>1310720</currentMemory>
    <maxMemory slots="16">4194304</maxMemory>
    <vcpu current="1">16</vcpu>
    <devices>
        <rng model="virtio">
            <backend model="random">{device}</backend>
        </rng>
        <disk device="cdrom" snapshot="no" type="file">
            <address bus="1" controller="0" target="0" type="drive" unit="0" />
            <source file="" startupPolicy="optional" />
            <target bus="ide" dev="hdc" />
            <readonly />
        </disk>
        <disk device="disk" snapshot="no" type="file">
            <address bus="0" controller="0" target="0" type="drive" unit="0" />
            <source file="{path}" />
            <target bus="scsi" dev="sda" />
            <serial>1234</serial>
            <boot order="1" />
            <driver cache="none" error_policy="stop" io="threads" name="qemu"
                    type="qcow2" />
        </disk>
        <disk device="disk" snapshot="no" type="file">
            <address bus="0" controller="0" target="1" type="drive" unit="0" />
            <source file="{second_disk_path}" />
            <target bus="scsi" dev="sdb" />
            <serial>5678</serial>
            <boot order="2" />
            <driver cache="none" error_policy="stop" io="threads" name="qemu"
                    type="qcow2" />
        </disk>
    </devices>
    <metadata>
        <ns0:qos />
        <ovirt-vm:vm>
            <ovirt-vm:device devtype="disk" name="sda">
                <ovirt-vm:imageID>111</ovirt-vm:imageID>
                <ovirt-vm:poolID>222</ovirt-vm:poolID>
                <ovirt-vm:volumeID>{volume_id}</ovirt-vm:volumeID>
                <ovirt-vm:domainID>333</ovirt-vm:domainID>
            </ovirt-vm:device>
        </ovirt-vm:vm>
    </metadata>
</domain>'''

    def test_restore_paths(self):
        xml = self.XML
        second_disk_path = '/path/secondary-drive'
        snapshot_params = {'path': '/path/snapshot-path',
                           'volume_id': 'aaa',
                           'device': '/dev/random',
                           'second_disk_path': second_disk_path,
                           }
        engine_params = {'path': '/path/engine-path',
                         'volume_id': 'bbb',
                         'device': '/dev/urandom',
                         'second_disk_path': second_disk_path,
                         }
        snapshot_xml = xml.format(**snapshot_params)
        engine_xml = xml.format(**engine_params)
        params = {'_srcDomXML': snapshot_xml,
                  'xml': engine_xml,
                  'restoreState': {
                      'device': 'disk',
                      'imageID': u'111',
                      'poolID': u'222',
                      'domainID': u'333',
                      'volumeID': u'bbb',
                  },
                  'restoreFromSnapshot': True,
                  }
        with fake.VM(params) as vm:
            vm._normalizeVdsmImg = lambda *args: None
            devices = vm._make_devices()
            vm_xml = vm.conf['xml']
        tested_drives = (('1234', engine_params['path'],),
                         ('5678', second_disk_path,),)
        for serial, path in tested_drives:
            for d in devices[hwclass.DISK]:
                if d.serial == serial:
                    assert d.path == path
                    break
            else:
                raise Exception('Tested drive not found', serial)
        dom = xmlutils.fromstring(vm_xml)
        random = vmxml.find_first(dom, 'backend')
        assert random.text == snapshot_params['device']
        for serial, path in tested_drives:
            for d in dom.findall(".//disk[serial='{}']".format(serial)):
                assert vmxml.find_attr(d, 'source', 'file') == path
                break
            else:
                raise Exception('Tested drive not found', serial)
        assert vm_xml == vm._domain.xml


class VncSecureTest(TestCaseBase):
    XML_NO_VNC = """<?xml version="1.0" encoding="utf-8"?>
        <domain type="kvm"
          xmlns:qemu="http://libvirt.org/schemas/domain/qemu/1.0">
          <devices>
            <graphics autoport="yes" keymap="en-us" passwd="*****"
                  passwdValidTo="1970-01-01T00:00:01" port="1234"
                  tlsPort="4321" type="spice">
              <listen network="vdsm-vmDisplay" type="network"/>
            </graphics>
          </devices>
        </domain>"""

    XML_VNC = """<?xml version="1.0" encoding="utf-8"?>
        <domain type="kvm"
          xmlns:qemu="http://libvirt.org/schemas/domain/qemu/1.0">
          <devices>
            <graphics autoport="yes" keymap="en-us" {passwd_tag}
                  {passwd_valid_tag} port="5900"
                  tlsPort="5900" type="vnc">
              <listen network="vdsm-vmDisplay" type="network"/>
            </graphics>
          </devices>
        </domain>"""

    NO_PASSWD = ''
    PASSWD_EMPTY = 'passwd = ""'
    PASSWD_PRESENT = 'passwd = "a-paSSword321"'
    NO_PASSWD_VALID = ''
    PASSWD_VALID_EMPTY = 'passwdValidTo = ""'
    PASSWD_VALID_PRESENT = 'passwdValidTo = "1970-01-01T00:00:01"'

    def test_no_vnc(self):
        assert graphics.is_vnc_secure({'xml': self.XML_NO_VNC},
                                      self.log)

    @MonkeyPatch(utils, 'sasl_enabled', lambda: False)
    def test_sasl_disabled_no_password(self):
        xml = self.XML_VNC.format(passwd_tag=self.NO_PASSWD,
                                  passwd_valid_tag=self.NO_PASSWD_VALID)
        assert not graphics.is_vnc_secure({'xml': xml}, self.log)
        xml = self.XML_VNC.format(passwd_tag=self.PASSWD_EMPTY,
                                  passwd_valid_tag=self.PASSWD_VALID_EMPTY)
        assert not graphics.is_vnc_secure({'xml': xml}, self.log)
        xml = self.XML_VNC.format(passwd_tag=self.PASSWD_PRESENT,
                                  passwd_valid_tag=self.NO_PASSWD_VALID)
        assert not graphics.is_vnc_secure({'xml': xml}, self.log)
        xml = self.XML_VNC.format(passwd_tag=self.PASSWD_PRESENT,
                                  passwd_valid_tag=self.PASSWD_VALID_EMPTY)
        assert not graphics.is_vnc_secure({'xml': xml}, self.log)
        xml = self.XML_VNC.format(passwd_tag=self.NO_PASSWD,
                                  passwd_valid_tag=self.PASSWD_VALID_PRESENT)
        assert graphics.is_vnc_secure({'xml': xml}, self.log)
        xml = self.XML_VNC.format(passwd_tag=self.PASSWD_EMPTY,
                                  passwd_valid_tag=self.PASSWD_VALID_PRESENT)
        assert graphics.is_vnc_secure({'xml': xml}, self.log)

    @MonkeyPatch(utils, 'sasl_enabled', lambda: False)
    def test_sasl_disabled_password(self):
        xml = self.XML_VNC.format(passwd_tag=self.PASSWD_PRESENT,
                                  passwd_valid_tag=self.PASSWD_VALID_PRESENT)
        assert graphics.is_vnc_secure({'xml': xml}, self.log)

    @MonkeyPatch(utils, 'sasl_enabled', lambda: True)
    def test_sasl_enabled_password(self):
        xml = self.XML_VNC.format(passwd_tag=self.PASSWD_PRESENT,
                                  passwd_valid_tag=self.PASSWD_VALID_PRESENT)
        assert graphics.is_vnc_secure({'xml': xml}, self.log)

    @MonkeyPatch(utils, 'sasl_enabled', lambda: True)
    def test_sasl_enabled_no_password(self):
        xml = self.XML_VNC.format(passwd_tag=self.NO_PASSWD,
                                  passwd_valid_tag=self.NO_PASSWD_VALID)
        assert graphics.is_vnc_secure({'xml': xml}, self.log)
