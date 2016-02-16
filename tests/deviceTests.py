#
# Copyright 2008-2015 Red Hat, Inc.
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

from vdsm import constants

from virt import vmdevices
from virt.vmdevices import hwclass
from virt.domain_descriptor import DomainDescriptor

from monkeypatch import MonkeyPatchScope
from testlib import permutations, expandPermutations
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
        <graphics autoport="yes" keymap="en-us" passwd="*****"
                  passwdValidTo="1970-01-01T00:00:01" port="-1" type="vnc">
            <listen network="vdsm-vmDisplay" type="network"/>
        </graphics>""",

        """
        <graphics autoport="yes" listen="0" passwd="*****"
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
        <graphics autoport="yes" listen="0" passwd="*****"
                  passwdValidTo="1970-01-01T00:00:01" port="-1"
                  tlsPort="-1" type="spice">
            <channel mode="secure" name="main"/>
        </graphics>""",

        """
        <graphics autoport="yes" listen="0" passwd="*****"
                  passwdValidTo="1970-01-01T00:00:01" port="-1"
                  tlsPort="-1" type="spice">
            <clipboard copypaste="no"/>
        </graphics>""",

        """
        <graphics autoport="yes" listen="0" passwd="*****"
                passwdValidTo="1970-01-01T00:00:01" port="-1"
                tlsPort="-1" type="spice">
            <filetransfer enable="no"/>
        </graphics>"""]

    def setUp(self):
        self.conf = {
            'vmName': 'testVm',
            'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
            'smp': '8', 'maxVCpus': '160',
            'memSize': '1024', 'memGuaranteedSize': '512'}

        self.confDisplayVnc = (
            {'display': 'vnc', 'displayNetwork': 'vmDisplay'},

            {'display': 'vnc', 'displayPort': '-1', 'displayNetwork':
             'vmDisplay', 'keyboardLayout': 'en-us'})

        self.confDisplaySpice = (
            {'display': 'qxl', 'displayNetwork': 'vmDisplay'},

            {'display': 'qxl', 'displayPort': '-1',
             'displaySecurePort': '-1'})

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

        self.confDisplay = self.confDisplayVnc + self.confDisplaySpice

        self.confDeviceGraphics = (self.confDeviceGraphicsVnc +
                                   self.confDeviceGraphicsSpice)

    def testGraphicsDeviceLegacy(self):
        for conf in self.confDisplay:
            conf.update(self.conf)
            with fake.VM(conf) as testvm:
                devs = testvm.devSpecMapFromConf()
                self.assertTrue(devs['graphics'])

    def testGraphicsDevice(self):
        for dev in self.confDeviceGraphics:
            with fake.VM(self.conf, dev) as testvm:
                devs = testvm.devSpecMapFromConf()
                self.assertTrue(devs['graphics'])

    def testGraphicDeviceHeadless(self):
        with fake.VM(self.conf) as testvm:
            devs = testvm.devSpecMapFromConf()
            self.assertFalse(devs['graphics'])

    def testGraphicsDeviceMixed(self):
        """
        if proper Graphics Devices are supplied, display* params must be
        ignored.
        """
        for conf in self.confDisplay:
            conf.update(self.conf)
            for dev in self.confDeviceGraphics:
                with fake.VM(self.conf, dev) as testvm:
                    devs = testvm.devSpecMapFromConf()
                    self.assertEqual(len(devs['graphics']), 1)
                    self.assertEqual(devs['graphics'][0]['device'],
                                     dev[0]['device'])

    def testGraphicsDeviceSanityLegacy(self):
        for conf in self.confDisplay:
            conf.update(self.conf)
            self.assertTrue(vmdevices.graphics.isSupportedDisplayType(conf))

    def testGraphicsDeviceSanity(self):
        for dev in self.confDeviceGraphics:
            conf = {'display': 'qxl', 'devices': list(dev)}
            conf.update(self.conf)
            self.assertTrue(vmdevices.graphics.isSupportedDisplayType(conf))

    def testGraphicDeviceUnsupported(self):
        conf = {'display': 'rdp'}
        conf.update(self.conf)
        self.assertFalse(vmdevices.graphics.isSupportedDisplayType(conf))

    def testGraphicDeviceHeadlessSupported(self):
        conf = {}
        conf.update(self.conf)
        self.assertTrue(vmdevices.graphics.isSupportedDisplayType(conf))

    def testHasSpiceLegacy(self):
        for conf in self.confDisplaySpice:
            conf.update(self.conf)
            with fake.VM(conf) as testvm:
                self.assertTrue(testvm.hasSpice)

        for conf in self.confDisplayVnc:
            conf.update(self.conf)
            with fake.VM(conf) as testvm:
                self.assertFalse(testvm.hasSpice)

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
            devs = testvm.devSpecMapFromConf()
            self.assertTrue(len(devs['graphics']) == 2)

    @permutations([['vnc'], ['spice']])
    def testGraphicsDeviceDuplicated(self, devType):
        devices = [{'type': 'graphics', 'device': devType},
                   {'type': 'graphics', 'device': devType}]
        with fake.VM(self.conf, devices) as testvm:
            self.assertRaises(ValueError, testvm.devSpecMapFromConf)

    def testSmartcardXML(self):
        smartcardXML = '<smartcard mode="passthrough" type="spicevmc"/>'
        dev = {'device': 'smartcard',
               'specParams': {'mode': 'passthrough', 'type': 'spicevmc'}}
        smartcard = vmdevices.core.Smartcard(self.conf, self.log, **dev)
        self.assertXMLEqual(smartcard.getXML().toxml(), smartcardXML)

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
        tpm = vmdevices.core.Tpm(self.conf, self.log, **dev)
        self.assertXMLEqual(tpm.getXML().toxml(), tpmXML)

    @permutations([[None], [{}], [{'enableSocket': False}]])
    def testConsolePtyXML(self, specParams):
        consoleXML = """
            <console type="pty">
                <target port="0" type="virtio"/>
            </console>"""
        dev = {'device': 'console'}
        if specParams is not None:
            dev['specParams'] = specParams
        console = vmdevices.core.Console(self.conf, self.log, **dev)
        self.assertXMLEqual(console.getXML().toxml(), consoleXML)

    def testConsoleSocketXML(self):
        consoleXML = """
            <console type="unix">
                <source mode="bind" path="%s%s.sock" />
                <target port="0" type="virtio"/>
            </console>""" % (constants.P_OVIRT_VMCONSOLES,
                             self.conf['vmId'])
        dev = {'device': 'console', 'specParams': {'enableSocket': True}}
        console = vmdevices.core.Console(self.conf, self.log, **dev)
        self.assertXMLEqual(console.getXML().toxml(), consoleXML)

    def testBalloonXML(self):
        balloonXML = '<memballoon model="virtio"/>'
        dev = {'device': 'memballoon', 'type': 'balloon',
               'specParams': {'model': 'virtio'}}
        balloon = vmdevices.core.Balloon(self.conf, self.log, **dev)
        self.assertXMLEqual(balloon.getXML().toxml(), balloonXML)

    def testRngXML(self):
        rngXML = """
            <rng model="virtio">
                <rate bytes="1234" period="2000"/>
                <backend model="random">/dev/random</backend>
            </rng>"""

        dev = {'type': 'rng', 'model': 'virtio', 'specParams':
               {'period': '2000', 'bytes': '1234', 'source': 'random'}}

        rng = vmdevices.core.Rng(self.conf, self.log, **dev)
        self.assertXMLEqual(rng.getXML().toxml(), rngXML)

    def testWatchdogXML(self):
        watchdogXML = '<watchdog action="none" model="i6300esb"/>'
        dev = {'device': 'watchdog', 'type': 'watchdog',
               'specParams': {'model': 'i6300esb', 'action': 'none'}}
        watchdog = vmdevices.core.Watchdog(self.conf, self.log, **dev)
        self.assertXMLEqual(watchdog.getXML().toxml(), watchdogXML)

    def testSoundXML(self):
        soundXML = '<sound model="ac97"/>'
        dev = {'device': 'ac97'}
        sound = vmdevices.core.Sound(self.conf, self.log, **dev)
        self.assertXMLEqual(sound.getXML().toxml(), soundXML)

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
        video = vmdevices.core.Video(self.conf, self.log, **dev_spec)
        self.assertXMLEqual(video.getXML().toxml(), video_xml)

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
               'custom': {'queues': '7'}}

        self.conf['custom'] = {'vhost': 'ovirtmgmt:true', 'sndbuf': '0'}
        iface = vmdevices.network.Interface(self.conf, self.log, **dev)
        self.assertXMLEqual(iface.getXML().toxml(), interfaceXML)

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
               'custom': {'queues': '7'}}
        self.conf['custom'] = {'vhost': 'ovirtmgmt:true', 'sndbuf': '0'}
        iface = vmdevices.network.Interface(self.conf, self.log, **dev)
        originalBandwidth = iface.getXML().getElementsByTagName('bandwidth')[0]
        self.assertXMLEqual(originalBandwidth.toxml(), originalBwidthXML)
        bandwith = iface.paramsToBandwidthXML(NEW_OUT, originalBandwidth)
        self.assertXMLEqual(bandwith.toxml(), updatedBwidthXML)

    def testControllerXML(self):
        devConfs = [
            {'device': 'ide', 'index': '0', 'address': self.PCI_ADDR_DICT},
            {'device': 'scsi', 'index': '0', 'model': 'virtio-scsi',
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
            <controller index="0" ports="16" type="virtio-serial">
                <address %s/>
            </controller>""",

            """
            <controller index="0" model="ich9-ehci1" type="usb">
                <master startport="0"/>
                <address %s/>
            </controller>"""]

        for devConf, xml in zip(devConfs, expectedXMLs):
            device = vmdevices.core.Controller(self.conf, self.log, **devConf)
            self.assertXMLEqual(device.getXML().toxml(), xml % self.PCI_ADDR)

    def testRedirXML(self):
        redirXML = """
            <redirdev type="spicevmc">
                <address %s/>
            </redirdev>""" % self.PCI_ADDR

        dev = {'device': 'spicevmc', 'address': self.PCI_ADDR_DICT}

        redir = vmdevices.core.Redir(self.conf, self.log, **dev)
        self.assertXMLEqual(redir.getXML().toxml(), redirXML)

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
                testvm.conf, testvm.log,
                device='spice', port='-1', tlsPort='-1')

            testvm.conf['devices'] = [graphConf]
            testvm._devices = {hwclass.GRAPHICS: [graphDev]}
            testvm._domain = DomainDescriptor(graphicsXML)

            testvm._getUnderlyingGraphicsDeviceInfo()

            self.assertEqual(graphDev.port, port)
            self.assertEqual(graphDev.tlsPort, tlsPort)
            self.assertEqual(graphDev.port, graphConf['port'])
            self.assertEqual(graphDev.tlsPort, graphConf['tlsPort'])

    def testLegacyGraphicsXML(self):
        vmConfs = [
            {'display': 'vnc', 'displayPort': '-1', 'displayNetwork':
             'vmDisplay', 'keyboardLayout': 'en-us'},

            {'display': 'qxl', 'displayPort': '-1', 'displaySecurePort': '-1',
             'spiceSecureChannels':
             "smain,sinputs,scursor,splayback,srecord,sdisplay"},

            {'display': 'qxl', 'displayPort': '-1', 'displaySecurePort': '-1',
             'spiceSecureChannels': "smain"},

            {'display': 'qxl', 'displayPort': '-1', 'displaySecurePort': '-1',
             'copyPasteEnable': 'false'},

            {'display': 'qxl', 'displayPort': '-1', 'displaySecurePort': '-1',
             'fileTransferEnable': 'false'}]

        for vmConf, xml in zip(vmConfs, self.GRAPHICS_XMLS):
            self._verifyGraphicsXML(vmConf, xml, isLegacy=True)

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
            self._verifyGraphicsXML(vmConf, xml, isLegacy=False)

    def _verifyGraphicsXML(self, vmConf, xml, isLegacy):
        spiceChannelXML = """
            <channel type="spicevmc">
                <target name="com.redhat.spice.0" type="virtio"/>
            </channel>"""

        vmConf.update(self.conf)
        with fake.VM(vmConf) as testvm:
            dev = (testvm.getConfGraphics() if isLegacy
                   else vmConf['devices'])[0]
            graph = vmdevices.graphics.Graphics(vmConf, self.log, **dev)
            self.assertXMLEqual(graph.getXML().toxml(), xml)

            if graph.device == 'spice':
                self.assertXMLEqual(graph.getSpiceVmcChannelsXML().toxml(),
                                    spiceChannelXML)

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
        memory = vmdevices.core.Memory(self.conf, self.log, **params)
        self.assertXMLEqual(memory.getXML().toxml(), memoryXML)

    def testGraphicsNoDisplayNetwork(self):
        with fake.VM() as testvm:
            graphDev = vmdevices.graphics.Graphics(
                testvm.conf, testvm.log)

            self.assertNotIn('displayNetwork', graphDev.specParams)

    def testGraphicsDisplayNetworkFromSpecParams(self):
        with fake.VM() as testvm:
            graphDev = vmdevices.graphics.Graphics(
                testvm.conf, testvm.log,
                specParams={'displayNetwork': 'vmDisplaySpecParams'})

            self.assertEqual(graphDev.specParams['displayNetwork'],
                             'vmDisplaySpecParams')

    def testGraphicsDisplayNetworkFromVmConf(self):
        conf = {'displayNetwork': 'vmDisplayConf'}
        conf.update(self.conf)
        with fake.VM(conf) as testvm:
            graphDev = vmdevices.graphics.Graphics(
                testvm.conf, testvm.log)

            self.assertEqual(graphDev.specParams['displayNetwork'],
                             'vmDisplayConf')

    def testGraphicsDisplayNetworkFromSpecParamsOverridesVmConf(self):
        conf = {'displayNetwork': 'vmDisplayConf'}
        conf.update(self.conf)
        with fake.VM(conf) as testvm:
            graphDev = vmdevices.graphics.Graphics(
                testvm.conf, testvm.log,
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
            dev = {'device': 'console'}
            con = vmdevices.core.Console(self.cfg, self.log, **dev)
            con.prepare()

            self.assertEqual(supervdsm.prepared_path, None)

    def test_console_usock_prepare_path(self):
        supervdsm = fake.SuperVdsm()
        with MonkeyPatchScope([(vmdevices.core, 'supervdsm', supervdsm)]):
            dev = {'device': 'console', 'specParams': {'enableSocket': True}}
            con = vmdevices.core.Console(self.cfg, self.log, **dev)
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
            dev = {'device': 'console'}
            con = vmdevices.core.Console(self.cfg, self.log, **dev)
            con.cleanup()

            self.assertEqual(self._cleaned_path, None)

    def test_console_usock_cleanup_path(self):
        def _fake_cleanup(path):
            self._cleaned_path = path

        with MonkeyPatchScope([(vmdevices.core,
                                'cleanup_guest_socket', _fake_cleanup)]):

            dev = {'device': 'console', 'specParams': {'enableSocket': True}}
            con = vmdevices.core.Console(self.cfg, self.log, **dev)
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
        # avail_map, output_sources
        [{'/dev/random': True, '/dev/hwrng': True}, ['random', 'hwrng']],
        [{'/dev/random': True, '/dev/hwrng': False}, ['random']],
        [{'/dev/random': False, '/dev/hwrng': True}, ['hwrng']],
        [{'/dev/random': False, '/dev/hwrng': False}, []],
    ])
    def test_available_sources(self, avail_map, output_sources):

        def fake_path_exists(path):
            return avail_map.get(path, False)

        with MonkeyPatchScope([(os.path, 'exists', fake_path_exists)]):
            available = list(sorted(vmdevices.core.Rng.available_sources()))

        expected = list(sorted(output_sources))
        self.assertEqual(available, expected)

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
        rng = vmdevices.core.Rng(self.conf, self.log, **dev_conf)
        self.assertTrue(rng.uses_source(source))
