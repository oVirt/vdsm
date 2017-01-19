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

import os.path

from vdsm.common import response
from vdsm import constants
from vdsm.virt import vmdevices
from vdsm.virt.domain_descriptor import DomainDescriptor
from vdsm.virt.vmdevices import graphics
from vdsm.virt.vmdevices import hwclass

from monkeypatch import MonkeyPatch, MonkeyPatchScope
from testlib import permutations, expandPermutations, make_config
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

    def testHasSpice(self):
        for dev in self.confDeviceGraphicsSpice:
            with fake.VM(self.conf, dev) as testvm:
                self.assertTrue(testvm.hasSpice)

        for dev in self.confDeviceGraphicsVnc:
            with fake.VM(self.conf, dev) as testvm:
                self.assertFalse(testvm.hasSpice)

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

    def testSmartcardXML(self):
        smartcardXML = '<smartcard mode="passthrough" type="spicevmc"/>'
        dev = {'device': 'smartcard',
               'specParams': {'mode': 'passthrough', 'type': 'spicevmc'}}
        smartcard = vmdevices.core.Smartcard(self.log, **dev)
        self.assert_dom_xml_equal(smartcard.getXML(), smartcardXML)

    def testTpmXML(self):
        tpmXML = """
            <tpm model="tpm-tis">
                <backend type="passthrough">
                    <device path="/dev/tpm0"/>
                </backend>
            </tpm>
            """
        dev = {'device': 'tpm',
               'specParams': {'mode': 'passthrough',
                              'path': '/dev/tpm0', 'model': 'tpm-tis'}}
        tpm = vmdevices.core.Tpm(self.log, **dev)
        self.assert_dom_xml_equal(tpm.getXML(), tpmXML)

    @permutations([[None], [{}], [{'enableSocket': False}]])
    def testConsolePtyXML(self, specParams):
        consoleXML = """
            <console type="pty">
                <target port="0" type="virtio"/>
            </console>"""
        dev = {
            'device': 'console',
            'vmid': self.conf['vmId'],
        }
        if specParams is not None:
            dev['specParams'] = specParams
        console = vmdevices.core.Console(self.log, **dev)
        self.assert_dom_xml_equal(console.getXML(), consoleXML)

    def testConsoleSocketXML(self):
        consoleXML = """
            <console type="unix">
                <source mode="bind" path="%s%s.sock" />
                <target port="0" type="virtio"/>
            </console>""" % (constants.P_OVIRT_VMCONSOLES,
                             self.conf['vmId'])
        dev = {
            'device': 'console',
            'specParams': {'enableSocket': True},
            'vmid': self.conf['vmId'],
        }
        console = vmdevices.core.Console(self.log, **dev)
        self.assert_dom_xml_equal(console.getXML(), consoleXML)

    def testBalloonXML(self):
        balloonXML = '<memballoon model="virtio"/>'
        dev = {'device': 'memballoon', 'type': 'balloon',
               'specParams': {'model': 'virtio'}}
        balloon = vmdevices.core.Balloon(self.log, **dev)
        self.assert_dom_xml_equal(balloon.getXML(), balloonXML)

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
            testvm._devices = testvm._devMapFromDevSpecMap(devs)
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

    def testRngXML(self):
        rngXML = """
            <rng model="virtio">
                <rate bytes="1234" period="2000"/>
                <backend model="random">/dev/random</backend>
            </rng>"""

        dev = {'type': 'rng', 'model': 'virtio', 'specParams':
               {'period': '2000', 'bytes': '1234', 'source': 'random'}}

        rng = vmdevices.core.Rng(self.log, **dev)
        self.assert_dom_xml_equal(rng.getXML(), rngXML)

    def testWatchdogXML(self):
        watchdogXML = '<watchdog action="none" model="i6300esb"/>'
        dev = {'device': 'watchdog', 'type': 'watchdog',
               'specParams': {'model': 'i6300esb', 'action': 'none'}}
        watchdog = vmdevices.core.Watchdog(self.log, **dev)
        self.assert_dom_xml_equal(watchdog.getXML(), watchdogXML)

    def testSoundXML(self):
        soundXML = '<sound model="ac97"/>'
        dev = {'device': 'ac97'}
        sound = vmdevices.core.Sound(self.log, **dev)
        self.assert_dom_xml_equal(sound.getXML(), soundXML)

    @permutations([
        [{'device': 'vga',
          'specParams': {'vram': '32768', 'heads': '2'}},
         """<video>
         <model heads="2" type="vga" vram="32768"/>
         </video>"""],
        [{'device': 'qxl',
          'specParams': {'vram': '65536', 'heads': '2', 'ram': '131072'}},
         """<video>
         <model heads="2" ram="131072" type="qxl" vram="65536"/>
         </video>"""],
        [{'device': 'qxl',
          'specParams': {'vram': '32768', 'heads': '2',
                         'ram': '65536', 'vgamem': '8192'}},
         """<video>
         <model heads="2" ram="65536" type="qxl" vgamem="8192" vram="32768"/>
         </video>"""]
    ])
    def testVideoXML(self, dev_spec, video_xml):
        video = vmdevices.core.Video(self.log, **dev_spec)
        self.assert_dom_xml_equal(video.getXML(), video_xml)

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: MockedProxy())
    def testInterfaceXML(self):
        interfaceXML = """
            <interface type="bridge"> <address %s/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge="ovirtmgmt"/>
                <filterref filter="no-mac-spoofing"/>
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
               'vm_custom': {'vhost': 'ovirtmgmt:true', 'sndbuf': '0'}}
        iface = vmdevices.network.Interface(self.log, **dev)
        self.assert_dom_xml_equal(iface.getXML(), interfaceXML)

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: MockedProxy())
    def testInterfaceXMLFilterParameters(self):
        interfaceXML = """
            <interface type="bridge"> <address %s/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge="ovirtmgmt"/>
                <filterref filter="clean-traffic">
                    <parameter name='IP' value='10.0.0.1'/>
                    <parameter name='IP' value='10.0.0.2'/>
                </filterref>
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
            'vm_custom': {'vhost': 'ovirtmgmt:true', 'sndbuf': '0'}}
        iface = vmdevices.network.Interface(self.log, **dev)
        self.assert_dom_xml_equal(iface.getXML(), interfaceXML)

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: MockedProxy(ovs_bridge='ovirtmgmt'))
    @MonkeyPatch(vmdevices.network.net_api, 'net2vlan', lambda x: 101)
    def testInterfaceOnOvsWithVlanXML(self):
        interfaceXML = """
            <interface type="bridge">
                <address %s/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge="ovirtmgmt"/>
                <virtualport type="openvswitch" />
                <vlan>
                    <tag id="101" />
                </vlan>
                <filterref filter="no-mac-spoofing"/>
                <boot order="1"/>
                <driver name="vhost" queues="7"/>
                <tune>
                    <sndbuf>0</sndbuf>
                </tune>
            </interface>""" % self.PCI_ADDR

        dev = {'nicModel': 'virtio', 'macAddr': '52:54:00:59:F5:3F',
               'network': 'ovirtmgmt', 'address': self.PCI_ADDR_DICT,
               'device': 'bridge', 'type': 'interface',
               'bootOrder': '1', 'filter': 'no-mac-spoofing',
               'custom': {'queues': '7'},
               'vm_custom': {'vhost': 'ovirtmgmt:true', 'sndbuf': '0'}}
        iface = vmdevices.network.Interface(self.log, **dev)
        self.assert_dom_xml_equal(iface.getXML(), interfaceXML)

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
                 'getProxy', lambda: MockedProxy(ovs_bridge='ovirtmgmt'))
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
              <bandwidth/>
            </interface>
        '''
        with fake.VM(devices=devices, create_device_objects=True) as testvm:
            testvm._dom = fake.Domain()
            res = testvm.updateDevice(params)
            self.assertFalse(response.is_error(res))
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

    def testControllerXML(self):
        devConfs = [
            {'device': 'ide', 'index': '0', 'address': self.PCI_ADDR_DICT},
            {'device': 'scsi', 'index': '0', 'model': 'virtio-scsi',
             'address': self.PCI_ADDR_DICT},
            {'device': 'scsi', 'index': '0', 'model': 'virtio-scsi',
             'address': self.PCI_ADDR_DICT, 'specParams': {}},
            {'device': 'scsi', 'model': 'virtio-scsi', 'index': '0',
             'specParams': {'ioThreadId': '0'},
             'address': self.PCI_ADDR_DICT},
            {'device': 'scsi', 'model': 'virtio-scsi', 'index': '0',
             'specParams': {'ioThreadId': 0},
             'address': self.PCI_ADDR_DICT},
            {'device': 'virtio-serial', 'address': self.PCI_ADDR_DICT},
            {'device': 'usb', 'model': 'ich9-ehci1', 'index': '0',
             'master': {'startport': '0'}, 'address': self.PCI_ADDR_DICT}]
        expectedXMLs = [
            """
            <controller index="0" type="ide">
                <address %s/>
            </controller>""",

            """
            <controller index="0" model="virtio-scsi" type="scsi">
                <address %s/>
            </controller>""",

            """
            <controller index="0" model="virtio-scsi" type="scsi">
                <address %s/>
            </controller>""",

            """
            <controller index="0" model="virtio-scsi" type="scsi">
                <address %s/>
                <driver iothread="0"/>
            </controller>""",

            """
            <controller index="0" model="virtio-scsi" type="scsi">
                <address %s/>
                <driver iothread="0"/>
            </controller>""",

            """
            <controller index="0" ports="16" type="virtio-serial">
                <address %s/>
            </controller>""",

            """
            <controller index="0" model="ich9-ehci1" type="usb">
                <master startport="0"/>
                <address %s/>
            </controller>"""]

        for devConf, xml in zip(devConfs, expectedXMLs):
            device = vmdevices.core.Controller(self.log, **devConf)
            self.assert_dom_xml_equal(device.getXML(), xml % self.PCI_ADDR)

    def testRedirXML(self):
        redirXML = """
            <redirdev type="spicevmc">
                <address %s/>
            </redirdev>""" % self.PCI_ADDR

        dev = {'device': 'spicevmc', 'address': self.PCI_ADDR_DICT}

        redir = vmdevices.core.Redir(self.log, **dev)
        self.assert_dom_xml_equal(redir.getXML(), redirXML)

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
            (vmdevices.graphics.net_api, 'libvirt_networks', lambda: {})
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

    def testMemoryDeviceXML(self):
        memoryXML = """<memory model='dimm'>
            <target>
                <size unit='KiB'>1048576</size>
                <node>0</node>
            </target>
        </memory>
        """
        params = {'device': 'memory', 'type': 'memory',
                  'size': 1024, 'node': 0}
        memory = vmdevices.core.Memory(self.log, **params)
        self.assert_dom_xml_equal(memory.getXML(), memoryXML)

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

    def testGraphicsDisplayNetworkFromVmConf(self):
        conf = {'displayNetwork': 'vmDisplayConf'}
        conf.update(self.conf)
        with fake.VM(conf) as testvm:
            dev = {'type': hwclass.GRAPHICS, 'specParams': {}}
            testvm._dev_spec_update_with_vm_conf(dev)
            graphDev = vmdevices.graphics.Graphics(testvm.log, **dev)
            self.assertEqual(graphDev.specParams['displayNetwork'],
                             'vmDisplayConf')


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


@expandPermutations
class TestDeviceHelpers(TestCaseBase):

    _DEVICES = [{'alias': 'dimm0', 'type': hwclass.MEMORY, 'size': 1024},
                {'alias': 'ac97', 'type': hwclass.SOUND}]

    @permutations([
        [hwclass.MEMORY, 'dimm0', 0],
        [hwclass.SOUND, 'ac97', 1],
    ])
    def test_lookup_conf(self, dev_type, alias, index):
        conf = vmdevices.common.lookup_conf_by_alias(self._DEVICES,
                                                     dev_type, alias)
        self.assertEqual(conf, self._DEVICES[index])

    @permutations([
        [hwclass.MEMORY, 'dimm1'],
        [hwclass.SOUND, 'dimm0'],
    ])
    def test_lookup_conf_error(self, dev_type, alias):
        self.assertRaises(LookupError,
                          vmdevices.common.lookup_conf_by_alias,
                          self._DEVICES, dev_type, alias)

    @permutations([
        [hwclass.MEMORY, 'dimm0'],
        [hwclass.SOUND, 'ac97'],
    ])
    def test_lookup_device(self, dev_type, alias):
        with fake.VM(devices=self._DEVICES, create_device_objects=True) as vm:
            dev = vmdevices.common.lookup_device_by_alias(vm._devices,
                                                          dev_type, alias)
            self.assertEqual(dev.alias, alias)

    @permutations([
        [hwclass.MEMORY, 'dimm1'],
        [hwclass.SOUND, 'dimm0'],
    ])
    def test_lookup_device_error(self, dev_type, alias):
        with fake.VM(devices=self._DEVICES, create_device_objects=True) as vm:
            self.assertRaises(LookupError,
                              vmdevices.common.lookup_device_by_alias,
                              vm._devices, dev_type, alias)


class MockedProxy(object):

    def __init__(self, ovs_bridge=None):
        self._ovs_bridge = ovs_bridge

    def ovs_bridge(self, name):
        return self._ovs_bridge
