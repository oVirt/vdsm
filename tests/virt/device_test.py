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
from __future__ import division

import os.path

from vdsm.common import hostdev
from vdsm.common import response
from vdsm.common import xmlutils
from vdsm import constants
import vdsm
import vdsm.virt
from vdsm.virt import vmdevices
from vdsm.virt import vmxml
from vdsm.virt.domain_descriptor import DomainDescriptor
from vdsm.virt.vmdevices import graphics
from vdsm.virt.vmdevices import hwclass

from monkeypatch import MonkeyPatch, MonkeyPatchScope
from testlib import permutations, expandPermutations, make_config, read_data
from testlib import VdsmTestCase as TestCaseBase
from testlib import XMLTestCase
import vmfakelib as fake


@expandPermutations
class TestVmDevices(XMLTestCase):

    PCI_ADDR = \
        'bus="0x00" domain="0x0000" function="0x0" slot="0x03" type="pci"'
    PCI_ADDR_DICT = {'slot': '0x03', 'bus': '0x00', 'domain': '0x0000',
                     'function': '0x0', 'type': 'pci'}

    GRAPHICS_XMLS = [
        """
        <graphics autoport="yes" defaultMode="secure"
                  keymap="en-us" passwd="*****"
                  passwdValidTo="1970-01-01T00:00:01" port="-1" type="vnc">
            <listen network="vdsm-vmDisplay" type="network"/>
        </graphics>""",

        """
        <graphics autoport="yes" defaultMode="secure"
                  listen="0" passwd="*****"
                  passwdValidTo="1970-01-01T00:00:01" port="-1"
                  tlsPort="-1" type="spice">
            <channel mode="secure" name="main"/>
            <channel mode="secure" name="inputs"/>
            <channel mode="secure" name="cursor"/>
            <channel mode="secure" name="playback"/>
            <channel mode="secure" name="record"/>
            <channel mode="secure" name="display"/>
        </graphics>""",

        """
        <graphics autoport="yes" defaultMode="secure"
                  listen="0" passwd="*****"
                  passwdValidTo="1970-01-01T00:00:01" port="-1"
                  tlsPort="-1" type="spice">
            <channel mode="secure" name="main"/>
        </graphics>""",

        """
        <graphics autoport="yes" defaultMode="secure"
                  listen="0" passwd="*****"
                  passwdValidTo="1970-01-01T00:00:01" port="-1"
                  tlsPort="-1" type="spice">
            <clipboard copypaste="no"/>
        </graphics>""",

        """
        <graphics autoport="yes" defaultMode="secure"
                listen="0" passwd="*****"
                passwdValidTo="1970-01-01T00:00:01" port="-1"
                tlsPort="-1" type="spice">
            <filetransfer enable="no"/>
        </graphics>"""]

    def setUp(self):
        self.conf = {
            'vmName': 'testVm',
            'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
            'smp': '8', 'maxVCpus': '160',
            'memSize': '1024', 'memGuaranteedSize': '512',
        }

        self.confDeviceGraphicsVnc = (
            ({'type': 'graphics', 'device': 'vnc'},),

            ({'type': 'graphics', 'device': 'vnc', 'port': '-1',
                'specParams': {
                    'displayNetwork': 'vmDisplay',
                    'keyMap': 'en-us'}},))

        self.confDeviceGraphicsSpice = (
            ({'type': 'graphics', 'device': 'spice'},),

            ({'type': 'graphics', 'device': 'spice', 'port': '-1',
                'tlsPort': '-1', 'specParams': {
                    'spiceSecureChannels':
                    'smain,sinputs,scursor,splayback,srecord,sdisplay'}},))

        self.confDeviceGraphics = (self.confDeviceGraphicsVnc +
                                   self.confDeviceGraphicsSpice)

    def test_createXmlElem(self):
        dev = {'type': 'graphics', 'device': 'spice'}
        expected_xml = '''<?xml version=\'1.0\' encoding=\'utf-8\'?>
        <graphics device="spice" type="test" />'''
        with fake.VM(self.conf, devices=(dev,),
                     create_device_objects=True) as testvm:
            graphics = testvm._devices[hwclass.GRAPHICS][0]
            element = graphics.createXmlElem('graphics', 'test',
                                             attributes=('device', 'foo',))
            result = xmlutils.tostring(element)
            self.assertXMLEqual(result, expected_xml)

    def testGraphicsDevice(self):
        for dev in self.confDeviceGraphics:
            with fake.VM(self.conf, dev) as testvm:
                devs = testvm._devSpecMapFromConf()
                self.assertTrue(devs['graphics'])

    def testGraphicDeviceHeadless(self):
        with fake.VM(self.conf) as testvm:
            devs = testvm._devSpecMapFromConf()
            self.assertFalse(devs['graphics'])

    def testGraphicDeviceHeadlessSupported(self):
        conf = {}
        conf.update(self.conf)
        self.assertTrue(vmdevices.graphics.isSupportedDisplayType(conf))

    def testHasSpiceEngineXML(self):
        conf = {}
        conf.update(self.conf)
        conf['xml'] = read_data('domain.xml')
        with fake.VM(conf) as testvm:
            self.assertTrue(testvm.hasSpice)

    @permutations([['vnc', 'spice'], ['spice', 'vnc']])
    def testGraphicsDeviceMultiple(self, primary, secondary):
        devices = [{'type': 'graphics', 'device': primary},
                   {'type': 'graphics', 'device': secondary}]
        with fake.VM(self.conf, devices) as testvm:
            devs = testvm._devSpecMapFromConf()
            self.assertEqual(len(devs['graphics']), 2)

    @permutations([['vnc'], ['spice']])
    def testGraphicsDeviceDuplicated(self, devType):
        devices = [{'type': 'graphics', 'device': devType},
                   {'type': 'graphics', 'device': devType}]
        with fake.VM(self.conf, devices) as testvm:
            self.assertRaises(ValueError, testvm._devSpecMapFromConf)

    @permutations([
        # alias, memballoonXML
        (None, "<memballoon model='none'/>"),
        ('balloon0',
         "<memballoon model='none'><alias name='balloon0'/></memballoon>"),
    ])
    def testBalloonDeviceAliasUpdateConfig(self, alias, memballoonXML):
        domainXML = """<domain>
        <devices>
        %s
        </devices>
        </domain>""" % memballoonXML
        dev = {'device': 'memballoon', 'type': 'none', 'specParams': {}}
        with fake.VM(self.conf, [dev]) as testvm:
            testvm._domain = DomainDescriptor(domainXML)
            devs = testvm._devSpecMapFromConf()
            testvm._updateDevices(devs)
            testvm._devices = vmdevices.common.dev_map_from_dev_spec_map(
                devs, testvm.log
            )
            self.assertNotRaises(
                vmdevices.core.Balloon.update_device_info,
                testvm,
                testvm._devices[hwclass.BALLOON],
            )
            dev = testvm._devices[hwclass.BALLOON][0]
            if alias is None:
                self.assertFalse(hasattr(dev, 'alias'))
            else:
                self.assertEqual(dev.alias, alias)

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: MockedProxy())
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

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: MockedProxy(
                     ovs_bridge={'name': 'ovirtmgmt', 'dpdk_enabled': False}))
    def test_interface_update(self):
        devices = [{'nicModel': 'virtio', 'network': 'ovirtmgmt',
                    'macAddr': '52:54:00:59:F5:3F',
                    'device': 'bridge', 'type': 'interface',
                    'alias': 'net1', 'name': 'net1',
                    'linkActive': 'true',
                    'specParams': {'inbound': {'average': 1000, 'peak': 5000,
                                               'burst': 1024},
                                   'outbound': {'average': 128, 'burst': 256}},
                    }]
        params = {'linkActive': 'true', 'alias': 'net1',
                  'deviceType': 'interface', 'network': 'ovirtmgmt2',
                  'specParams': {'inbound': {}, 'outbound': {}}}
        updated_xml = '''
            <interface type="bridge">
              <mac address="52:54:00:59:F5:3F"/>
              <model type="virtio"/>
              <source bridge="ovirtmgmt2"/>
              <virtualport type="openvswitch"/>
              <link state="up"/>
              <alias name="net1"/>
              <bandwidth/>
            </interface>
        '''
        with fake.VM(devices=devices, create_device_objects=True) as testvm:
            testvm._dom = fake.Domain()
            res = testvm.updateDevice(params)
            self.assertIn('vmList', res)
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

            self.assertEqual(interface_dev.driver,
                             {'queues': '10', 'name': 'vfio'})

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

            self.assertEqual(nic.driver, saved_driver)

    @MonkeyPatch(vmdevices.network.supervdsm, 'getProxy',
                 lambda: MockedProxy(ovs_bridge={'name': 'test',
                                                 'dpdk_enabled': True}))
    def test_vhostuser_interface(self):
        interfaceXML = """
        <interface type="vhostuser"> <address {pciaddr}/>
            <mac address="52:54:00:59:F5:3F"/>
            <model type="virtio"/>
            <source mode="server" path="{rundir}vhostuser/{vmid}"
                type="unix" />
            <filterref filter="no-mac-spoofing"/>
            <link state="up"/>
            <boot order="1"/>
        </interface>""".format(
            pciaddr=self.PCI_ADDR,
            rundir=constants.P_VDSM_RUN,
            vmid='f773dff7-0e9c-3bc3-9e36-9713415446df',
        )

        dev = {'nicModel': 'virtio', 'macAddr': '52:54:00:59:F5:3F',
               'network': 'test', 'address': self.PCI_ADDR_DICT,
               'device': 'bridge', 'type': 'interface',
               'bootOrder': '1', 'filter': 'no-mac-spoofing',
               'vmid': self.conf['vmId']}

        iface = vmdevices.network.Interface(self.log, **dev)
        iface.setup()
        try:
            self.assert_dom_xml_equal(iface.getXML(), interfaceXML)
        finally:
            iface.teardown()

    @MonkeyPatch(vmdevices.network.supervdsm, 'getProxy',
                 lambda: MockedProxy(ovs_bridge={'name': 'test',
                                                 'dpdk_enabled': True}))
    def test_vhostuser_interface_recovery(self):
        interfaceXML = """
        <interface type="vhostuser"> <address {pciaddr}/>
            <mac address="52:54:00:59:F5:3F"/>
            <model type="virtio"/>
            <source mode="server" path="{rundir}vhostuser/{vmid}"
                type="unix" />
            <filterref filter="no-mac-spoofing"/>
            <link state="up"/>
            <boot order="1"/>
        </interface>""".format(
            pciaddr=self.PCI_ADDR,
            rundir=constants.P_VDSM_RUN,
            vmid='f773dff7-0e9c-3bc3-9e36-9713415446df',
        )

        dev = {'nicModel': 'virtio', 'macAddr': '52:54:00:59:F5:3F',
               'network': 'test', 'address': self.PCI_ADDR_DICT,
               'device': 'bridge', 'type': 'interface',
               'bootOrder': '1', 'filter': 'no-mac-spoofing',
               'vmid': self.conf['vmId']}

        iface = vmdevices.network.Interface(self.log, **dev)
        iface.recover()
        try:
            self.assert_dom_xml_equal(iface.getXML(), interfaceXML)
        finally:
            iface.teardown()

    def testGetUnderlyingGraphicsDeviceInfo(self):
        port = '6000'
        tlsPort = '6001'
        graphicsXML = """<?xml version="1.0" encoding="utf-8"?>
        <domain type="kvm"
          xmlns:qemu="http://libvirt.org/schemas/domain/qemu/1.0">
          <devices>
            <graphics autoport="yes" keymap="en-us" passwd="*****"
                  passwdValidTo="1970-01-01T00:00:01" port="%s"
                  tlsPort="%s" type="spice">
              <listen network="vdsm-vmDisplay" type="network"/>
            </graphics>
         </devices>
        </domain>""" % (port, tlsPort)
        with fake.VM() as testvm:
            graphConf = {
                'type': hwclass.GRAPHICS, 'device': 'spice',
                'port': '-1', 'tlsPort': '-1'}
            graphDev = vmdevices.graphics.Graphics(
                testvm.log,
                device='spice', port='-1', tlsPort='-1')

            testvm.conf['devices'] = [graphConf]
            device_conf = [graphDev]
            testvm._domain = DomainDescriptor(graphicsXML)

            vmdevices.graphics.Graphics.update_device_info(testvm, device_conf)

            self.assertEqual(graphDev.port, port)
            self.assertEqual(graphDev.tlsPort, tlsPort)
            self.assertEqual(graphDev.port, graphConf['port'])
            self.assertEqual(graphDev.tlsPort, graphConf['tlsPort'])

    @MonkeyPatch(graphics, 'config', make_config([('vars', 'ssl', 'true')]))
    def testGraphicsDeviceXML(self):
        vmConfs = [
            {'devices': [{
                'type': 'graphics', 'device': 'vnc', 'port': '-1',
                'specParams': {
                    'displayNetwork': 'vmDisplay',
                    'keyMap': 'en-us'}}]},

            {'devices': [{
                'type': 'graphics', 'device': 'spice', 'port': '-1',
                'tlsPort': '-1', 'specParams': {
                    'spiceSecureChannels':
                        'smain,sinputs,scursor,splayback,srecord,sdisplay'}}]},

            {'devices': [{
                'type': 'graphics', 'device': 'spice', 'port': '-1',
                'tlsPort': '-1', 'specParams': {
                    'spiceSecureChannels': 'smain'}}]},

            {'devices': [{
                'type': 'graphics', 'device': 'spice', 'port': '-1',
                'tlsPort': '-1', 'specParams': {
                    'copyPasteEnable': 'false'}}]},

            {'devices': [{
                'type': 'graphics', 'device': 'spice', 'port': '-1',
                'tlsPort': '-1', 'specParams': {
                    'fileTransferEnable': 'false'}}]}]

        for vmConf, xml in zip(vmConfs, self.GRAPHICS_XMLS):
            self._verifyGraphicsXML(vmConf, xml)

    def _verifyGraphicsXML(self, vmConf, xml):
        spiceChannelXML = """
            <channel type="spicevmc">
                <target name="com.redhat.spice.0" type="virtio"/>
            </channel>"""

        vmConf.update(self.conf)
        with fake.VM() as testvm:
            dev = testvm._dev_spec_update_with_vm_conf(vmConf['devices'][0])
        with MonkeyPatchScope([
            (vmdevices.graphics.libvirtnetwork, 'networks', lambda: {})
        ]):
            graph = vmdevices.graphics.Graphics(self.log, **dev)
        self.assert_dom_xml_equal(graph.getXML(), xml)

        if graph.device == 'spice':
            self.assert_dom_xml_equal(graph.getSpiceVmcChannelsXML(),
                                      spiceChannelXML)

    @permutations([['''<hostdev managed="no" mode="subsystem" type="usb">
                          <alias name="testusb"/>
                          <source>
                             <address bus="1" device="2"/>
                          </source>
                        </hostdev>''',
                    {'type': hwclass.HOSTDEV, 'device': 'usb_1_1'}],
                   ['''<hostdev managed="no" mode="subsystem" type="pci">
                         <alias name="testpci"/>
                         <source>
                           <address bus="0" domain="0" function="0" slot="2"/>
                         </source>
                         <address bus="0" domain="0" function="0" slot="3"/>
                       </hostdev>''',
                    {'type': hwclass.HOSTDEV, 'device': 'pci_0000_00_02_0'}]])
    def testGetUpdateHostDeviceInfo(self, device_xml, conf):
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <domain type="kvm"
          xmlns:qemu="http://libvirt.org/schemas/domain/qemu/1.0">
          <devices>
            %s
          </devices>
        </domain>""" % (device_xml,)
        with fake.VM() as testvm:
            device = vmdevices.hostdevice.HostDevice(testvm.log, **conf)

            testvm.conf['devices'] = [conf]
            device_conf = [device]
            testvm._domain = DomainDescriptor(xml)

            vmdevices.hostdevice.HostDevice.update_device_info(testvm,
                                                               device_conf)

    def test_mdev_details_(self):
        details = hostdev._mdev_type_details('graphics-card-1', '/nonexistent')
        for f in hostdev._MDEV_FIELDS:
            self.assertEqual(getattr(details, f),
                             'graphics-card-1' if f == 'name' else '')

    def testGraphicsNoDisplayNetwork(self):
        with fake.VM() as testvm:
            graphDev = vmdevices.graphics.Graphics(testvm.log)

            self.assertNotIn('displayNetwork', graphDev.specParams)

    def testGraphicsDisplayNetworkFromSpecParams(self):
        with fake.VM() as testvm:
            graphDev = vmdevices.graphics.Graphics(
                testvm.log,
                specParams={'displayNetwork': 'vmDisplaySpecParams'})

            self.assertEqual(graphDev.specParams['displayNetwork'],
                             'vmDisplaySpecParams')


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
            dev = {
                'device': 'console',
                'vmid': self.cfg['vmId'],
            }
            con = vmdevices.core.Console(self.log, **dev)
            con.prepare()

            self.assertEqual(supervdsm.prepared_path, None)

    def test_console_usock_prepare_path(self):
        supervdsm = fake.SuperVdsm()
        with MonkeyPatchScope([(vmdevices.core, 'supervdsm', supervdsm)]):
            dev = {
                'device': 'console',
                'specParams': {'enableSocket': True},
                'vmid': self.cfg['vmId'],
            }
            con = vmdevices.core.Console(self.log, **dev)
            con.prepare()

            self.assertEqual(supervdsm.prepared_path,
                             self._expected_path)
            self.assertEqual(supervdsm.prepared_path_group,
                             constants.OVIRT_VMCONSOLE_GROUP)

    def test_console_pty_not_cleanup_path(self):
        def _fake_cleanup(path):
            self._cleaned_path = path

        with MonkeyPatchScope([(vmdevices.core,
                                'cleanup_guest_socket', _fake_cleanup)]):
            dev = {
                'device': 'console',
                'vmId': self.cfg['vmId'],
            }
            con = vmdevices.core.Console(self.log, **dev)
            con.cleanup()

            self.assertEqual(self._cleaned_path, None)

    def test_console_usock_cleanup_path(self):
        def _fake_cleanup(path):
            self._cleaned_path = path

        with MonkeyPatchScope([(vmdevices.core,
                                'cleanup_guest_socket', _fake_cleanup)]):

            dev = {
                'device': 'console',
                'specParams': {'enableSocket': True},
                'vmid': self.cfg['vmId'],
            }
            con = vmdevices.core.Console(self.log, **dev)
            con.cleanup()

            self.assertEqual(self._cleaned_path, self._expected_path)


@expandPermutations
class RngTests(TestCaseBase):

    def setUp(self):
        self.conf = {
            'vmName': 'testVm',
            'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
            'smp': '8', 'maxVCpus': '160',
            'memSize': '1024', 'memGuaranteedSize': '512',
        }

    @permutations([
        # config, source
        ['random', '/dev/random'],
        ['hwrng', '/dev/hwrng'],
    ])
    def test_matching_source(self, config, source):
        conf = {
            'type': 'rng',
            'model': 'virtio',
            'specParams': {
                'period': '2000',
                'bytes': '1234',
                'source': config,
            },
        }
        self.assertTrue(vmdevices.core.Rng.matching_source(conf, source))

    @permutations([
        # config, source
        ['random', '/dev/random'],
        ['hwrng', '/dev/hwrng'],
    ])
    def test_uses_source(self, config, source):
        dev_conf = {
            'type': 'rng',
            'model': 'virtio',
            'specParams': {
                'period': '2000',
                'bytes': '1234',
                'source': config,
            },
        }
        rng = vmdevices.core.Rng(self.log, **dev_conf)
        self.assertTrue(rng.uses_source(source))


class BrokenSuperVdsm(fake.SuperVdsm):

    def setPortMirroring(self, network, nic_name):
        if self.mirrored_networks:
            raise Exception("Too many networks")
        super(BrokenSuperVdsm, self).setPortMirroring(network, nic_name)


@expandPermutations
class TestHotplug(TestCaseBase):

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
        <ovirt-vm:domainID>1111</ovirt-vm:domainID>
        <ovirt-vm:imageID>1234</ovirt-vm:imageID>
        <ovirt-vm:poolID>2222</ovirt-vm:poolID>
        <ovirt-vm:volumeID>3333</ovirt-vm:volumeID>
        <ovirt-vm:volumeChain>
            <ovirt-vm:volumeChainNode>
              <ovirt-vm:domainID>1111</ovirt-vm:domainID>
              <ovirt-vm:imageID>1234</ovirt-vm:imageID>
              <ovirt-vm:leaseOffset type="int">0</ovirt-vm:leaseOffset>
              <ovirt-vm:leasePath>/path/to.lease</ovirt-vm:leasePath>
              <ovirt-vm:path>/path/to/disk</ovirt-vm:path>
              <ovirt-vm:volumeID>3333</ovirt-vm:volumeID>
            </ovirt-vm:volumeChainNode>
        </ovirt-vm:volumeChain>
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
</hotplug>
'''

    def setUp(self):
        devices = [{'nicModel': 'virtio', 'network': 'ovirtmgmt',
                    'macAddr': "11:22:33:44:55:66",
                    'device': 'bridge', 'type': 'interface',
                    'alias': 'net1', 'name': 'net1',
                    'linkActive': 'true',
                    }]
        with fake.VM(devices=devices, create_device_objects=True) as vm:
            vm._dom = fake.Domain(vm=vm)
            self.vm = vm
        self.supervdsm = fake.SuperVdsm()

    def test_disk_hotplug(self):
        vm = self.vm
        params = {'xml': self.DISK_HOTPLUG}
        supervdsm = fake.SuperVdsm()
        with MonkeyPatchScope([(vmdevices.network, 'supervdsm', supervdsm)]):
            vm.hotplugDisk(params)
        self.assertEqual(len(vm.getDiskDevices()), 1)
        dev = vm._devices[hwclass.DISK][0]
        self.assertEqual(dev.serial, '1234')
        self.assertEqual(dev.domainID, '1111')
        self.assertEqual(dev.name, 'sda')

    def test_disk_hotunplug(self):
        vm = self.vm
        params = {'xml': self.DISK_HOTPLUG}
        vm.hotunplugDisk(params)
        self.assertEqual(len(vm.getDiskDevices()), 0)

    def test_nic_hotplug(self):
        vm = self.vm
        self.assertEqual(len(vm._devices[hwclass.NIC]), 1)
        params = {'xml': self.NIC_HOTPLUG}
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm.hotplugNic(params)
        self.assertEqual(len(vm._devices[hwclass.NIC]), 2)
        for dev in vm._devices[hwclass.NIC]:
            if dev.macAddr == "66:55:44:33:22:11":
                break
        else:
            raise Exception("Hot plugged device not found")
        self.assertEqual(dev.macAddr, "66:55:44:33:22:11")
        self.assertEqual(dev.network, "test")
        # TODO: Make sure metadata of the original device is initialized in the
        # fake VM.
        # with vm._md_desc.device(mac_address="11:22:33:44:55:66") as dev:
        #     self.assertEqual(dev['network'], "ovirtmgmt")
        with vm._md_desc.device(mac_address="66:55:44:33:22:11") as dev:
            self.assertEqual(dev['network'], "test")
        self.assertEqual(self.supervdsm.mirrored_networks,
                         [('network1', '',),
                          ('network2', '',)])

    def test_nic_hotplug_mirroring_failure(self):
        vm = self.vm
        supervdsm = BrokenSuperVdsm()
        self.assertEqual(len(vm._devices[hwclass.NIC]), 1)
        params = {'xml': self.NIC_HOTPLUG}
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                supervdsm.getProxy)]):
            vm._waitForDeviceRemoval = lambda device: None
            vm.hotplugNic(params)
        self.assertEqual(len(vm._devices[hwclass.NIC]), 1)
        dev = vm._devices[hwclass.NIC][0]
        self.assertEqual(dev.macAddr, "11:22:33:44:55:66")
        self.assertEqual(dev.network, "ovirtmgmt")
        # TODO: Make sure metadata of the original device is initialized in the
        # fake VM.
        # with vm._md_desc.device(mac_address="11:22:33:44:55:66") as dev:
        #     self.assertEqual(dev['network'], "ovirtmgmt")
        with vm._md_desc.device(dev_type=hwclass.NIC,
                                mac_address="66:55:44:33:22:11") as dev:
            self.assertNotIn('network', dev)
        self.assertEqual(supervdsm.mirrored_networks, [])

    def test_nic_hotunplug(self):
        vm = self.vm
        self.test_nic_hotplug()
        self.assertEqual(len(vm._devices[hwclass.NIC]), 2)
        params = {'xml': self.NIC_HOTPLUG}
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm._waitForDeviceRemoval = lambda device: None
            vm.hotunplugNic(params)
        self.assertEqual(len(vm._devices[hwclass.NIC]), 1)
        dev = vm._devices[hwclass.NIC][0]
        self.assertEqual(dev.macAddr, "11:22:33:44:55:66")
        self.assertEqual(dev.network, "ovirtmgmt")
        # TODO: Make sure metadata of the original device is initialized in the
        # fake VM.
        # with vm._md_desc.device(mac_address="11:22:33:44:55:66") as dev:
        #     self.assertEqual(dev['network'], "ovirtmgmt")
        with vm._md_desc.device(dev_type=hwclass.NIC,
                                mac_addres="66:55:44:33:22:11") as dev:
            self.assertNotIn('network', dev)
        self.assertEqual(self.supervdsm.mirrored_networks, [])

    def test_nic_hotunplug_timeout(self):
        vm = self.vm
        self.test_nic_hotplug()
        self.assertEqual(len(vm._devices[hwclass.NIC]), 2)
        params = {'xml': self.NIC_HOTPLUG}
        with MonkeyPatchScope([
                (vdsm.common.supervdsm, 'getProxy', self.supervdsm.getProxy),
                (vdsm.virt.vm, 'config',
                 make_config([('vars', 'hotunplug_timeout', '0'),
                              ('vars', 'hotunplug_check_interval', '0.01')])),
        ]):
            self.vm._dom.vm = None
            self.assertTrue(response.is_error(vm.hotunplugNic(params)))
        self.assertEqual(len(vm._devices[hwclass.NIC]), 2)

    @permutations([
        ['virtio', 'pv'],
        ['novirtio', 'novirtio'],
    ])
    def test_legacy_conf_conversion(self, xml_model, conf_model):
        xml = self.NIC_HOTPLUG.replace('virtio', xml_model)
        vm = self.vm
        params = {'xml': xml}
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm.hotplugNic(params)
        dev_conf = vm.status()['devices']
        for conf in dev_conf:
            if conf.get('macAddr') == '66:55:44:33:22:11':
                self.assertEqual(conf.get('nicModel'), conf_model)
                break
        else:
            raise Exception("Hot plugged device not found")

    def test_legacy_nic_hotplug(self):
        vm = self.vm
        params = {'nic': {'macAddr': '66:55:44:33:22:11',
                          'network': 'test',
                          'device': 'bridge',
                          'type': 'interface',
                          }}
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm.hotplugNic(params)
        self.assertEqual(len(vm._devices[hwclass.NIC]), 2)
        hotplugged_devs = [dev for dev in vm._devices[hwclass.NIC]
                           if dev.macAddr == "66:55:44:33:22:11"]
        self.assertEqual(len(hotplugged_devs), 1)
        dev = hotplugged_devs[0]
        self.assertEqual(dev.network, "test")

    def test_legacy_nic_hotplug_port_mirroring(self):
        vm = self.vm
        port_mirroring = ['network1', 'network2']
        params = {'nic': {'macAddr': '66:55:44:33:22:11',
                          'network': 'test',
                          'device': 'bridge',
                          'type': 'interface',
                          'portMirroring': port_mirroring,
                          }}
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm.hotplugNic(params)
        self.assertEqual(len(vm._devices[hwclass.NIC]), 2)
        hotplugged_devs = [dev for dev in vm._devices[hwclass.NIC]
                           if dev.macAddr == "66:55:44:33:22:11"]
        self.assertEqual(len(hotplugged_devs), 1)
        dev = hotplugged_devs[0]
        self.assertEqual(dev.network, "test")
        self.assertEqual([net for net, _ in self.supervdsm.mirrored_networks],
                         port_mirroring)

    def test_legacy_nic_hotplug_sriov(self):
        vm = self.vm
        params = {'nic': {'macAddr': '66:55:44:33:22:11',
                          'device': hwclass.HOSTDEV,
                          'hostdev': 'pci_0000_00_00_0',
                          'type': 'interface',
                          }}
        dev_params = {
            'address': {
                'domain': '0x0000',
                'bus': '0x04',
                'slot': '0x01',
                'function': '0x3',
            }
        }
        with MonkeyPatchScope([
                (vdsm.common.supervdsm, 'getProxy', self.supervdsm.getProxy),
                (vmdevices.network, 'get_device_params', lambda _: dev_params),
                (vmdevices.network, 'detach_detachable', lambda _: None),
        ]):
            vm.hotplugNic(params)
        self.assertEqual(len(vm._devices[hwclass.NIC]), 2)
        hotplugged_devs = [dev for dev in vm._devices[hwclass.NIC]
                           if dev.macAddr == "66:55:44:33:22:11"]
        self.assertEqual(len(hotplugged_devs), 1)
        dev = hotplugged_devs[0]

    def test_legacy_nic_hotplug_sriov_port_mirroring(self):
        vm = self.vm
        port_mirroring = ['network1', 'network2']
        params = {'nic': {'macAddr': '66:55:44:33:22:11',
                          'device': hwclass.HOSTDEV,
                          'hostdev': 'pci_0000_00_00_0',
                          'type': 'interface',
                          'portMirroring': port_mirroring,
                          }}
        dev_params = {
            'address': {
                'domain': '0x0000',
                'bus': '0x04',
                'slot': '0x01',
                'function': '0x3',
            }
        }
        with MonkeyPatchScope([
                (vdsm.common.supervdsm, 'getProxy', self.supervdsm.getProxy),
                (vmdevices.network, 'get_device_params', lambda _: dev_params),
                (vmdevices.network, 'detach_detachable', lambda _: None),
        ]):
            vm.hotplugNic(params)
        self.assertEqual(len(vm._devices[hwclass.NIC]), 2)
        hotplugged_devs = [dev for dev in vm._devices[hwclass.NIC]
                           if dev.macAddr == "66:55:44:33:22:11"]
        self.assertEqual(len(hotplugged_devs), 1)
        dev = hotplugged_devs[0]
        self.assertEqual([net for net, _ in self.supervdsm.mirrored_networks],
                         port_mirroring)

    def test_legacy_nic_hotunplug(self):
        vm = self.vm
        params = {'nic': {'macAddr': '66:55:44:33:22:11',
                          'network': 'test',
                          'device': 'bridge',
                          'type': 'interface',
                          }}
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm.hotplugNic(params)
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm._waitForDeviceRemoval = lambda device: None
            vm.hotunplugNic(params)
        self.assertEqual(len(vm._devices[hwclass.NIC]), 1)
        dev = vm._devices[hwclass.NIC][0]
        self.assertEqual(dev.macAddr, "11:22:33:44:55:66")
        self.assertEqual(dev.network, "ovirtmgmt")

    def test_legacy_nic_hotunplug_port_mirroring(self):
        vm = self.vm
        port_mirroring = ['network1', 'network2']
        params = {'nic': {'macAddr': '66:55:44:33:22:11',
                          'network': 'test',
                          'device': 'bridge',
                          'type': 'interface',
                          'portMirroring': port_mirroring,
                          }}
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm._waitForDeviceRemoval = lambda device: None
            vm.hotplugNic(params)
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm.hotunplugNic(params)
        self.assertEqual(len(vm._devices[hwclass.NIC]), 1)
        dev = vm._devices[hwclass.NIC][0]
        self.assertEqual(dev.macAddr, "11:22:33:44:55:66")
        self.assertEqual(dev.network, "ovirtmgmt")
        self.assertEqual(self.supervdsm.mirrored_networks, [])

    def test_legacy_nic_hotplug_mirroring_failure(self):
        vm = self.vm
        supervdsm = BrokenSuperVdsm()
        params = {'nic': {'macAddr': '66:55:44:33:22:11',
                          'network': 'test',
                          'device': 'bridge',
                          'type': 'interface',
                          'portMirroring': ['network1', 'network2']}}
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                supervdsm.getProxy)]):
            vm._waitForDeviceRemoval = lambda device: None
            vm.hotplugNic(params)
        self.assertEqual(len(vm._devices[hwclass.NIC]), 1)
        dev = vm._devices[hwclass.NIC][0]
        self.assertEqual(dev.macAddr, "11:22:33:44:55:66")
        self.assertEqual(dev.network, "ovirtmgmt")
        with vm._md_desc.device(dev_type=hwclass.NIC,
                                mac_address="66:55:44:33:22:11") as dev:
            self.assertNotIn('network', dev)
        self.assertEqual(supervdsm.mirrored_networks, [])


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
      {mtu}
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
        devices = [{'nicModel': 'virtio', 'network': 'ovirtmgmt',
                    'macAddr': "11:22:33:44:55:66",
                    'device': 'bridge', 'type': 'interface',
                    'alias': 'net1', 'name': 'net1',
                    'linkActive': False,
                    }]
        with fake.VM(devices=devices, create_device_objects=True) as vm:
            vm._dom = fake.Domain()
            self.vm = vm
        self.supervdsm = fake.SuperVdsm()

    @permutations([
        # mtu_old, mtu_new
        (None, None),
        (1492, 1492),
    ])
    def test_nic_update(self, mtu_old, mtu_new):
        vm = self.vm
        self.assertEqual(len(vm._devices[hwclass.NIC]), 1)
        vm._devices[hwclass.NIC][0].mtu = mtu_old
        mtu = ''
        if mtu_new is not None:
            mtu = '<mtu size="%d" />' % mtu_new
        params = {
            'deviceType': 'interface',
            'xml': self.NIC_UPDATE.format(mtu=mtu),
        }
        with MonkeyPatchScope([(vdsm.common.supervdsm, 'getProxy',
                                self.supervdsm.getProxy)]):
            vm.updateDevice(params)
        self.assertEqual(len(vm._devices[hwclass.NIC]), 1)
        for dev in vm._devices[hwclass.NIC]:
            if dev.macAddr == "11:22:33:44:55:66":
                break
        else:
            raise Exception("Hot plugged device not found")
        self.assertTrue(dev.linkActive)
        self.assertEqual(dev.network, 'test')
        self.assertEqual(
            sorted(dev.portMirroring),
            sorted(['network1', 'network2'])
        )
        self.assertEqual(dev.mtu, mtu_new)


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
        # Check that unrelated devices are taken from the snapshot untouched,
        # not from the XML provided from Engine:
        for d in devices[hwclass.RNG]:
            self.assertEqual(d.specParams['source'],
                             os.path.basename(snapshot_params['device']))
            break
        else:
            raise Exception('RNG device not found')
        tested_drives = (('1234', engine_params['path'],),
                         ('5678', second_disk_path,),)
        for serial, path in tested_drives:
            for d in devices[hwclass.DISK]:
                if d.serial == serial:
                    self.assertEqual(d.path, path)
                    break
            else:
                raise Exception('Tested drive not found', serial)
        dom = xmlutils.fromstring(vm_xml)
        random = vmxml.find_first(dom, 'backend')
        self.assertEqual(random.text, snapshot_params['device'])
        for serial, path in tested_drives:
            for d in dom.findall(".//disk[serial='{}']".format(serial)):
                self.assertEqual(vmxml.find_attr(d, 'source', 'file'), path)
                break
            else:
                raise Exception('Tested drive not found', serial)
        self.assertEqual(vm_xml, vm._domain.xml)


class MockedProxy(object):

    def __init__(self, ovs_bridge=None):
        self._ovs_bridge = ovs_bridge

    def ovs_bridge(self, name):
        return self._ovs_bridge

    def add_ovs_vhostuser_port(self, bridge, port, socket):
        pass

    def remove_ovs_port(self, bridge, port):
        pass
