#
# Copyright IBM Corp. 2012
# Copyright 2013-2014 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from contextlib import contextmanager
from itertools import product
import re
import shutil
import tempfile
import threading
import time
import xml.etree.ElementTree as ET

import libvirt

from virt import vm
from virt import vmexitreason
from virt import vmstatus
from virt.vmtune import ioTuneMerge, ioTuneDomToValues, ioTuneToDom
from vdsm import constants
from vdsm import define
from testrunner import VdsmTestCase as TestCaseBase
from testrunner import permutations, expandPermutations, namedTemporaryDir
import caps
from vdsm import utils
from vdsm import libvirtconnection
from monkeypatch import MonkeyPatch, MonkeyPatchScope
from vmTestsData import CONF_TO_DOMXML_X86_64
from vmTestsData import CONF_TO_DOMXML_PPC64
from vmTestsData import CONF_TO_DOMXML_NO_VDSM

from testValidation import slowtest


class ConnectionMock:
    def __init__(self, *args):
        pass

    def domainEventRegisterAny(self, *arg):
        pass

    def listAllNetworks(self, *args):
        return []


class FakeClientIF(object):
    def __init__(self, *args, **kwargs):
        self.channelListener = None
        self.vmContainer = {}


class FakeDomain(object):
    def __init__(self, xml='',
                 virtError=libvirt.VIR_ERR_OK,
                 domState=libvirt.VIR_DOMAIN_RUNNING,
                 vmId=''):
        self._xml = xml
        self.devXml = ''
        self._virtError = virtError
        self._domState = domState
        self._vmId = vmId
        self._metadata = ""

    def _failIfRequested(self):
        if self._virtError != libvirt.VIR_ERR_OK:
            err = libvirt.libvirtError(defmsg='')
            err.err = [self._virtError]
            raise err

    def UUIDString(self):
        return self._vmId

    def info(self):
        self._failIfRequested()
        return (self._domState, )

    def XMLDesc(self, unused):
        return self._xml

    def updateDeviceFlags(self, devXml, unused):
        self.devXml = devXml

    def vcpusFlags(self, flags):
        return -1

    def metadata(self, type, uri, flags):
        self._failIfRequested()

        if not self._metadata:
            e = libvirt.libvirtError("No metadata")
            e.err = [libvirt.VIR_ERR_NO_DOMAIN_METADATA]
            raise e
        return self._metadata

    def setMetadata(self, type, xml, prefix, uri, flags):
        self._metadata = xml

    def schedulerParameters(self):
        return {'vcpu_quota': vm._NO_CPU_QUOTA,
                'vcpu_period': vm._NO_CPU_PERIOD}


class TestVm(TestCaseBase):

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
        </graphics>"""]

    def __init__(self, *args, **kwargs):
        TestCaseBase.__init__(self, *args, **kwargs)
        self.conf = {'vmName': 'testVm',
                     'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
                     'smp': '8', 'maxVCpus': '160',
                     'memSize': '1024', 'memGuaranteedSize': '512'}

    def assertXML(self, element, expectedXML, path=None):
        if path is None:
            converted = element.toprettyxml()
        else:
            elem = ET.fromstring(element.toprettyxml())
            converted = re.sub(' />', '/>',
                               ET.tostring(elem.find("./%s" % path)))
        self.assertEqual(re.sub('\n\s*', ' ', converted).strip(' '),
                         re.sub('\n\s*', ' ', expectedXML).strip(' '))

    def assertXMLNone(self, element, path):
        elem = ET.fromstring(element.toprettyxml())
        converted = elem.find("./%s" % path)
        self.assertEqual(converted, None)

    def assertBuildCmdLine(self, confToDom):
        oldVdsmRun = constants.P_VDSM_RUN
        constants.P_VDSM_RUN = tempfile.mkdtemp()
        try:
            for conf, expectedXML in confToDom:

                expectedXML = expectedXML % conf

                testVm = vm.Vm(self, conf)

                output = testVm._buildCmdLine()

                self.assertEqual(re.sub('\n\s*', ' ', output.strip(' ')),
                                 re.sub('\n\s*', ' ', expectedXML.strip(' ')))
        finally:
            shutil.rmtree(constants.P_VDSM_RUN)
            constants.P_VDSM_RUN = oldVdsmRun

    def testDomXML(self):
        expectedXML = """
           <domain type="kvm">
              <name>testVm</name>
              <uuid>9ffe28b6-6134-4b1e-8804-1185f49c436f</uuid>
              <memory>1048576</memory>
              <currentMemory>1048576</currentMemory>
              <vcpu current="8">160</vcpu>
              <devices/>
           </domain>"""

        domxml = vm._DomXML(self.conf, self.log,
                            caps.Architecture.X86_64)
        self.assertXML(domxml.dom, expectedXML)

    def testOSXMLBootMenu(self):
        vmConfs = (
            # trivial cases first
            {},
            {'bootMenuEnable': 'true'},
            {'bootMenuEnable': 'false'},
            {'bootMenuEnable': True},
            {'bootMenuEnable': False},
            # next with more fields
            {'bootMenuEnable': True,
             'kernelArgs': 'console=ttyS0 1'},
            {'bootMenuEnable': False,
             'kernelArgs': 'console=ttyS0 1'})
        expectedXMLs = ("""
            <os>
                 <type arch="x86_64" machine="pc">hvm</type>
                 <smbios mode="sysinfo"/>
            </os>""", """
            <os>
                 <type arch="x86_64" machine="pc">hvm</type>
                 <smbios mode="sysinfo"/>
                 <bootmenu enable="yes"/>
            </os>""", """
            <os>
                 <type arch="x86_64" machine="pc">hvm</type>
                 <smbios mode="sysinfo"/>
            </os>""", """
            <os>
                 <type arch="x86_64" machine="pc">hvm</type>
                 <smbios mode="sysinfo"/>
                 <bootmenu enable="yes"/>
            </os>""", """
            <os>
                 <type arch="x86_64" machine="pc">hvm</type>
                 <smbios mode="sysinfo"/>
            </os>""", """
            <os>
                 <type arch="x86_64" machine="pc">hvm</type>
                 <cmdline>console=ttyS0 1</cmdline>
                 <smbios mode="sysinfo"/>
                 <bootmenu enable="yes"/>
            </os>""",  """
            <os>
                 <type arch="x86_64" machine="pc">hvm</type>
                 <cmdline>console=ttyS0 1</cmdline>
                 <smbios mode="sysinfo"/>
            </os>""")
        for conf, xmlout in zip(vmConfs, expectedXMLs):
            conf.update(self.conf)
            domxml = vm._DomXML(conf, self.log,
                                caps.Architecture.X86_64)
            domxml.appendOs()
            self.assertXML(domxml.dom, xmlout, 'os')

    def testOSXMLX86_64(self):
        expectedXMLs = ["""
            <os>
                <type arch="x86_64" machine="pc">hvm</type>
                <initrd>/tmp/initrd-2.6.18.img</initrd>
                <kernel>/tmp/vmlinuz-2.6.18</kernel>
                <cmdline>console=ttyS0 1</cmdline>
                <smbios mode="sysinfo"/>
            </os>"""]
        vmConfs = [{'kernel': '/tmp/vmlinuz-2.6.18', 'initrd':
                   '/tmp/initrd-2.6.18.img', 'kernelArgs': 'console=ttyS0 1'}]

        OSXML = """
            <os>
                 <type arch="x86_64" machine="pc">hvm</type>
                 <boot dev="%s"/>
                 <smbios mode="sysinfo"/>
            </os>"""

        qemu2libvirtBoot = {'a': 'fd', 'c': 'hd', 'd': 'cdrom', 'n': 'network'}
        for k, v in qemu2libvirtBoot.iteritems():
            vmConfs.append({'boot': k})
            expectedXMLs.append(OSXML % v)

        for vmConf, xml in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = vm._DomXML(vmConf, self.log,
                                caps.Architecture.X86_64)
            domxml.appendOs()
            self.assertXML(domxml.dom, xml, 'os')

    def testOSPPCXML(self):
        expectedXMLs = ["""
            <os>
                <type arch="ppc64" machine="pseries">hvm</type>
                <initrd>/tmp/initrd-2.6.18.img</initrd>
                <kernel>/tmp/vmlinuz-2.6.18</kernel>
                <cmdline>console=ttyS0 1</cmdline>
            </os>"""]
        vmConfs = [{'kernel': '/tmp/vmlinuz-2.6.18', 'initrd':
                   '/tmp/initrd-2.6.18.img', 'kernelArgs': 'console=ttyS0 1'}]

        OSXML = """
            <os>
                 <type arch="ppc64" machine="pseries">hvm</type>
                 <boot dev="%s"/>
            </os>"""

        qemu2libvirtBoot = {'a': 'fd', 'c': 'hd', 'd': 'cdrom', 'n': 'network'}
        for k, v in qemu2libvirtBoot.iteritems():
            vmConfs.append({'boot': k})
            expectedXMLs.append(OSXML % v)

        for vmConf, xml in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = vm._DomXML(vmConf, self.log,
                                caps.Architecture.PPC64)
            domxml.appendOs()
            self.assertXML(domxml.dom, xml, 'os')

    def testSmartcardXML(self):
        smartcardXML = '<smartcard mode="passthrough" type="spicevmc"/>'
        dev = {'device': 'smartcard',
               'specParams': {'mode': 'passthrough', 'type': 'spicevmc'}}
        smartcard = vm.SmartCardDevice(self.conf, self.log, **dev)
        self.assertXML(smartcard.getXML(), smartcardXML)

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
        tpm = vm.TpmDevice(self.conf, self.log, **dev)
        self.assertXML(tpm.getXML(), tpmXML)

    def testFeaturesXML(self):
        featuresXML = """
            <features>
                  <acpi/>
            </features>"""
        domxml = vm._DomXML(self.conf, self.log,
                            caps.Architecture.X86_64)
        domxml.appendFeatures()
        self.assertXML(domxml.dom, featuresXML, 'features')

    def testFeaturesHyperVXML(self):
        featuresXML = """
            <features>
                  <acpi/>
                  <hyperv>
                         <relaxed state="on"/>
                  </hyperv>
            </features>"""
        conf = {'hypervEnable': 'true'}
        conf.update(self.conf)
        domxml = vm._DomXML(conf, self.log,
                            caps.Architecture.X86_64)
        domxml.appendFeatures()
        self.assertXML(domxml.dom, featuresXML, 'features')

    def testSysinfoXML(self):
        sysinfoXML = """
            <sysinfo type="smbios">
              <system>
                <entry name="manufacturer">%s</entry>
                <entry name="product">%s</entry>
                <entry name="version">%s</entry>
                <entry name="serial">%s</entry>
                <entry name="uuid">%s</entry>
              </system>
            </sysinfo>"""
        product = 'oVirt Node'
        version = '17-1'
        serial = 'A5955881-519B-11CB-8352-E78A528C28D8_00:21:cc:68:d7:38'
        sysinfoXML = sysinfoXML % (constants.SMBIOS_MANUFACTURER,
                                   product, version, serial, self.conf['vmId'])
        domxml = vm._DomXML(self.conf, self.log,
                            caps.Architecture.X86_64)
        domxml.appendSysinfo(product, version, serial)
        self.assertXML(domxml.dom, sysinfoXML, 'sysinfo')

    def testConsoleXML(self):
        consoleXML = """
            <console type="pty">
                <target port="0" type="virtio"/>
            </console>"""
        dev = {'device': 'console'}
        console = vm.ConsoleDevice(self.conf, self.log, **dev)
        self.assertXML(console.getXML(), consoleXML)

    def testClockXML(self):
        clockXML = """
            <clock adjustment="-3600" offset="variable">
                <timer name="rtc" tickpolicy="catchup"/>
                <timer name="pit" tickpolicy="delay"/>
                <timer name="hpet" present="no"/>
            </clock>"""
        self.conf['timeOffset'] = '-3600'
        domxml = vm._DomXML(self.conf, self.log,
                            caps.Architecture.X86_64)
        domxml.appendClock()
        self.assertXML(domxml.dom, clockXML, 'clock')

    def testHyperVClockXML(self):
        clockXML = """
            <clock adjustment="-3600" offset="variable">
                <timer name="rtc" tickpolicy="catchup" track="guest"/>
                <timer name="pit" tickpolicy="delay"/>
                <timer name="hpet" present="no"/>
            </clock>"""
        conf = {'timeOffset': '-3600', 'hypervEnable': 'true'}
        conf.update(self.conf)
        domxml = vm._DomXML(conf, self.log,
                            caps.Architecture.X86_64)
        domxml.appendClock()
        self.assertXML(domxml.dom, clockXML, 'clock')

    def testCpuXML(self):
        cpuXML = """
          <cpu match="exact">
              <model>Opteron_G4</model>
              <feature name="sse4.1" policy="require"/>
              <feature name="sse4.2" policy="require"/>
              <feature name="svm" policy="disable"/>
              <topology cores="2" sockets="40" threads="2"/>
              <numa>
                  <cell cpus="0-1" memory="5242880"/>
                  <cell cpus="2,3" memory="5242880"/>
              </numa>
          </cpu> """
        cputuneXML = """
          <cputune>
              <vcpupin cpuset="2-3" vcpu="1"/>
              <vcpupin cpuset="0-1" vcpu="0"/>
          </cputune> """

        numatuneXML = """
          <numatune>
              <memory mode="strict" nodeset="0-1"/>
          </numatune> """

        vmConf = {'cpuType': "Opteron_G4,+sse4_1,+sse4_2,-svm",
                  'smpCoresPerSocket': 2, 'smpThreadsPerCore': 2,
                  'cpuPinning': {'0': '0-1', '1': '2-3'},
                  'numaTune': {'mode': 'strict', 'nodeset': '0-1'},
                  'guestNumaNodes': [{'cpus': '0-1', 'memory': '5120',
                                      'nodeIndex': 0},
                                     {'cpus': '2,3', 'memory': '5120',
                                      'nodeIndex': 1}]}
        vmConf.update(self.conf)
        domxml = vm._DomXML(vmConf, self.log,
                            caps.Architecture.X86_64)
        domxml.appendCpu()
        self.assertXML(domxml.dom, cpuXML, 'cpu')
        self.assertXML(domxml.dom, cputuneXML, 'cputune')

        domxml.appendNumaTune()
        self.assertXML(domxml.dom, numatuneXML, 'numatune')

    def testChannelXML(self):
        channelXML = """
          <channel type="unix">
             <target name="%s" type="virtio"/>
             <source mode="bind" path="%s"/>
          </channel>"""
        path = '/tmp/channel-socket'
        name = 'org.linux-kvm.port.0'
        channelXML = channelXML % (name, path)
        domxml = vm._DomXML(self.conf, self.log,
                            caps.Architecture.X86_64)
        domxml._appendAgentDevice(path, name)
        self.assertXML(domxml.dom, channelXML, 'devices/channel')

    def testInputXMLX86_64(self):
        expectedXMLs = [
            """<input bus="ps2" type="mouse"/>""",
            """<input bus="usb" type="tablet"/>"""]

        vmConfs = [{}, {'tabletEnable': 'true'}]
        for vmConf, xml in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = vm._DomXML(vmConf, self.log,
                                caps.Architecture.X86_64)
            domxml.appendInput()
            self.assertXML(domxml.dom, xml, 'devices/input')

    def testInputXMLPPC64(self):
        expectedXMLs = [
            """<input bus="usb" type="mouse"/>""",
            """<input bus="usb" type="tablet"/>"""]

        vmConfs = [{}, {'tabletEnable': 'true'}]
        for vmConf, xml in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = vm._DomXML(vmConf, self.log,
                                caps.Architecture.PPC64)
            domxml.appendInput()
            self.assertXML(domxml.dom, xml, 'devices/input')

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
             'copyPasteEnable': 'false'}]

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
                    'copyPasteEnable': 'false'}}]}]

        for vmConf, xml in zip(vmConfs, self.GRAPHICS_XMLS):
            self._verifyGraphicsXML(vmConf, xml, isLegacy=False)

    def _verifyGraphicsXML(self, vmConf, xml, isLegacy):
        spiceChannelXML = """
            <channel type="spicevmc">
                <target name="com.redhat.spice.0" type="virtio"/>
            </channel>"""

        vmConf.update(self.conf)
        with FakeVM(vmConf) as fake:
            dev = (fake.getConfGraphics() if isLegacy
                   else vmConf['devices'])[0]
            graph = vm.GraphicsDevice(vmConf, self.log, **dev)
            self.assertXML(graph.getXML(), xml)

            if graph.device == 'spice':
                self.assertXML(graph.getSpiceVmcChannelsXML(),
                               spiceChannelXML)

    def testBalloonXML(self):
        balloonXML = '<memballoon model="virtio"/>'
        dev = {'device': 'memballoon', 'type': 'balloon',
               'specParams': {'model': 'virtio'}}
        balloon = vm.BalloonDevice(self.conf, self.log, **dev)
        self.assertXML(balloon.getXML(), balloonXML)

    def testRngXML(self):
        rngXML = """
            <rng model="virtio">
                <rate bytes="1234" period="2000"/>
                <backend model="random">/dev/random</backend>
            </rng>"""

        dev = {'type': 'rng', 'model': 'virtio', 'specParams':
               {'period': '2000', 'bytes': '1234', 'source': 'random'}}

        rng = vm.RngDevice(self.conf, self.log, **dev)
        self.assertXML(rng.getXML(), rngXML)

    def testWatchdogXML(self):
        watchdogXML = '<watchdog action="none" model="i6300esb"/>'
        dev = {'device': 'watchdog', 'type': 'watchdog',
               'specParams': {'model': 'i6300esb', 'action': 'none'}}
        watchdog = vm.WatchdogDevice(self.conf, self.log, **dev)
        self.assertXML(watchdog.getXML(), watchdogXML)

    def testSoundXML(self):
        soundXML = '<sound model="ac97"/>'
        dev = {'device': 'ac97'}
        sound = vm.SoundDevice(self.conf, self.log, **dev)
        self.assertXML(sound.getXML(), soundXML)

    def testVideoXML(self):
        videoXML = """
            <video>
                <model heads="2" type="vga" vram="32768"/>
            </video>"""

        dev = {'device': 'vga', 'specParams': {'vram': '32768',
               'heads': '2'}}
        video = vm.VideoDevice(self.conf, self.log, **dev)
        self.assertXML(video.getXML(), videoXML)

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
        iface = vm.NetworkInterfaceDevice(self.conf, self.log, **dev)
        self.assertXML(iface.getXML(), interfaceXML)

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
        iface = vm.NetworkInterfaceDevice(self.conf, self.log, **dev)
        originalBandwidth = iface.getXML().getElementsByTagName('bandwidth')[0]
        self.assertXML(originalBandwidth, originalBwidthXML)
        self.assertXML(iface.paramsToBandwidthXML(NEW_OUT, originalBandwidth),
                       updatedBwidthXML)

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
            dev = vm.ControllerDevice(self.conf, self.log, **devConf)
            self.assertXML(dev.getXML(), xml % self.PCI_ADDR)

    def testRedirXML(self):
        redirXML = """
            <redirdev type="spicevmc">
                <address %s/>
            </redirdev>""" % self.PCI_ADDR

        dev = {'device': 'spicevmc', 'address': self.PCI_ADDR_DICT}

        redir = vm.RedirDevice(self.conf, self.log, **dev)
        self.assertXML(redir.getXML(), redirXML)

    def testDriveSharedStatus(self):
        sharedConfigs = [
            # Backward compatibility
            {'shared': True}, {'shared': 'True'}, {'shared': 'true'},
            {'shared': False}, {'shared': 'False'}, {'shared': 'false'},
            # Missing shared definition
            {},
            # New extended values
            {'shared': 'exclusive'}, {'shared': 'shared'}, {'shared': 'none'},
            {'shared': 'transient'},
        ]

        expectedStates = [
            # Backward compatibility
            'shared', 'shared', 'shared', 'none', 'none', 'none',
            # Missing shared definition
            'none',
            # New extended values
            'exclusive', 'shared', 'none', 'transient',
        ]

        driveConfig = {'index': '0', 'iface': 'virtio', 'device': 'disk'}

        for driveInput, driveOutput in zip(sharedConfigs, expectedStates):
            driveInput.update(driveConfig)
            drive = vm.Drive({}, self.log, **driveInput)
            self.assertEqual(drive.extSharedState, driveOutput)

        # Negative flow, unsupported value
        driveInput.update({'shared': 'UNKNOWN-VALUE'})

        with self.assertRaises(ValueError):
            drive = vm.Drive({}, self.log, **driveInput)

    def testDriveXML(self):
        SERIAL = '54-a672-23e5b495a9ea'
        devConfs = [
            {'index': '2', 'propagateErrors': 'off', 'iface': 'ide',
             'name': 'hdc', 'format': 'raw', 'device': 'cdrom',
             'path': '/tmp/fedora.iso', 'type': 'disk', 'readonly': 'True',
             'shared': 'none', 'serial': SERIAL},

            {'index': '0', 'propagateErrors': 'on', 'iface': 'virtio',
             'name': 'vda', 'format': 'cow', 'device': 'disk',
             'path': '/tmp/disk1.img', 'type': 'disk', 'readonly': 'False',
             'shared': 'shared', 'serial': SERIAL,
             'specParams': {'ioTune': {'read_bytes_sec': 6120000,
                                       'total_iops_sec': 800}}},

            {'index': '0', 'propagateErrors': 'off', 'iface': 'virtio',
             'name': 'vda', 'format': 'raw', 'device': 'disk',
             'path': '/dev/mapper/lun1', 'type': 'disk', 'readonly': 'False',
             'shared': 'none', 'serial': SERIAL},

            {'index': '0', 'propagateErrors': 'off', 'iface': 'scsi',
             'name': 'sda', 'format': 'raw', 'device': 'disk',
             'path': '/tmp/disk1.img', 'type': 'disk', 'readonly': 'False',
             'shared': 'exclusive', 'serial': SERIAL},

            {'index': '0', 'propagateErrors': 'off', 'iface': 'scsi',
             'name': 'sda', 'format': 'raw', 'device': 'lun',
             'path': '/dev/mapper/lun1', 'type': 'disk', 'readonly': 'False',
             'shared': 'none', 'serial': SERIAL, 'sgio': 'unfiltered'}]

        expectedXMLs = [
            """
            <disk device="cdrom" snapshot="no" type="file">
                <source file="/tmp/fedora.iso" startupPolicy="optional"/>
                <target bus="ide" dev="hdc"/>
                <readonly/>
                <serial>%s</serial>
            </disk>""",

            """
            <disk device="disk" snapshot="no" type="file">
                <source file="/tmp/disk1.img"/>
                <target bus="virtio" dev="vda"/>
                <shareable/>
                <serial>%s</serial>
                <driver cache="writethrough" error_policy="enospace"
                        io="threads" name="qemu" type="qcow2"/>
                <iotune>
                    <read_bytes_sec>6120000</read_bytes_sec>
                    <total_iops_sec>800</total_iops_sec>
                </iotune>
            </disk>""",

            """
            <disk device="disk" snapshot="no" type="block">
                <source dev="/dev/mapper/lun1"/>
                <target bus="virtio" dev="vda"/>
                <serial>%s</serial>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="raw"/>
            </disk>""",

            """
            <disk device="disk" snapshot="no" type="file">
                <source file="/tmp/disk1.img"/>
                <target bus="scsi" dev="sda"/>
                <serial>%s</serial>
                <driver cache="none" error_policy="stop"
                        io="threads" name="qemu" type="raw"/>
            </disk>""",

            """
            <disk device="lun" sgio="unfiltered" snapshot="no" type="block">
                <source dev="/dev/mapper/lun1"/>
                <target bus="scsi" dev="sda"/>
                <serial>%s</serial>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="raw"/>
            </disk>"""]

        blockDevs = [False, False, True, False, True]
        vmConfs = [{}, {'custom': {'viodiskcache': 'writethrough'}},
                   {}, {}, {}]

        for (devConf, xml, blockDev, vmConf) in \
                zip(devConfs, expectedXMLs, blockDevs, vmConfs):
            drive = vm.Drive(vmConf, self.log, **devConf)
            # Patch Drive.blockDev to skip the block device checking.
            drive._blockDev = blockDev
            self.assertXML(drive.getXML(), xml % SERIAL)

    def testIoTuneException(self):
        SERIAL = '54-a672-23e5b495a9ea'
        basicConf = {'index': '0', 'propagateErrors': 'on', 'iface': 'virtio',
                     'name': 'vda', 'format': 'cow', 'device': 'disk',
                     'path': '/tmp/disk1.img', 'type': 'disk',
                     'readonly': 'False', 'shared': 'True', 'serial': SERIAL}
        tuneConfs = [
            {'read_iops_sec': 1000, 'total_iops_sec': 2000},
            {'read_bytes_sec': -5},
            {'aaa': 100},
            {'read_iops_sec': 'aaa'}]

        devConfs = [dict(specParams=dict(ioTune=tuneConf), **basicConf)
                    for tuneConf in tuneConfs]

        expectedExceptMsgs = [
            'A non-zero total value and non-zero read/write value for'
            ' iops_sec can not be set at the same time',
            'parameter read_bytes_sec value should be equal or greater'
            ' than zero',
            'parameter aaa name is invalid',
            'an integer is required for ioTune parameter read_iops_sec']

        vmConf = {'custom': {'viodiskcache': 'writethrough'}}

        for (devConf, exceptionMsg) in \
                zip(devConfs, expectedExceptMsgs):
            drive = vm.Drive(vmConf, self.log, **devConf)
            # Patch Drive.blockDev to skip the block device checking.
            drive._blockDev = False

            with self.assertRaises(Exception) as cm:
                drive.getXML()

            self.assertEquals(cm.exception.args[0], exceptionMsg)

    @MonkeyPatch(caps, 'getTargetArch', lambda: caps.Architecture.X86_64)
    @MonkeyPatch(caps, 'osversion', lambda: {
        'release': '1', 'version': '18', 'name': 'Fedora'})
    @MonkeyPatch(constants, 'SMBIOS_MANUFACTURER', 'oVirt')
    @MonkeyPatch(constants, 'SMBIOS_OSNAME', 'oVirt Node')
    @MonkeyPatch(libvirtconnection, 'get', ConnectionMock)
    @MonkeyPatch(utils, 'getHostUUID',
                 lambda: "fc25cbbe-5520-4f83-b82e-1541914753d9")
    def testBuildCmdLineX86_64(self):
        self.assertBuildCmdLine(CONF_TO_DOMXML_X86_64)

    @MonkeyPatch(caps, 'getTargetArch', lambda: caps.Architecture.PPC64)
    @MonkeyPatch(caps, 'osversion', lambda: {
        'release': '1', 'version': '18', 'name': 'Fedora'})
    @MonkeyPatch(libvirtconnection, 'get', ConnectionMock)
    @MonkeyPatch(utils, 'getHostUUID',
                 lambda: "fc25cbbe-5520-4f83-b82e-1541914753d9")
    def testBuildCmdLinePPC64(self):
        self.assertBuildCmdLine(CONF_TO_DOMXML_PPC64)

    def testGetVmPolicySucceded(self):
        with FakeVM() as fake:
            fake._dom = FakeDomain()
            self.assertXML(fake._getVmPolicy(), '<qos/>')

    def testGetVmPolicyEmptyOnNoMetadata(self):
        with FakeVM() as fake:
            fake._dom = FakeDomain(
                virtError=libvirt.VIR_ERR_NO_DOMAIN_METADATA)
            self.assertXML(fake._getVmPolicy(), '<qos/>')

    def testGetVmPolicyFailOnNoDomain(self):
        with FakeVM() as fake:
            fake._dom = FakeDomain(virtError=libvirt.VIR_ERR_NO_DOMAIN)
            self.assertEqual(fake._getVmPolicy(), None)

    def _xml_sanitizer(self, text):
        return re.sub(">[\t\n ]+<", "><", text).strip()

    def testUpdateVmPolicy(self):
        with FakeVM() as machine:
            dom = FakeDomain()
            machine._dom = dom

            policy = {
                "vcpuLimit": 50,
                "ioTune": [
                    {
                        "name": "test-device-by-name",
                        "maximum": {
                            "total_bytes_sec": 200, "total_iops_sec": 201,
                            "read_bytes_sec": 202, "read_iops_sec": 203,
                            "write_bytes_sec": 204, "write_iops_sec": 205
                        },
                        "guaranteed": {
                            "total_bytes_sec": 100, "total_iops_sec": 101,
                            "read_bytes_sec": 102, "read_iops_sec": 103,
                            "write_bytes_sec": 104, "write_iops_sec": 105
                        }
                    },
                    {
                        "path": "test-device-by-path",
                        "maximum": {
                            "total_bytes_sec": 400, "total_iops_sec": 401,
                            "read_bytes_sec": 402, "read_iops_sec": 403,
                            "write_bytes_sec": 404, "write_iops_sec": 405
                        },
                        "guaranteed": {
                            "total_bytes_sec": 300, "total_iops_sec": 301,
                            "read_bytes_sec": 302, "read_iops_sec": -1,
                            "write_bytes_sec": 304, "write_iops_sec": 305
                        }
                    }
                ]
            }

            machine.updateVmPolicy(policy)

            expected_xml = self._xml_sanitizer(u"""
            <qos>
                <vcpuLimit>50</vcpuLimit>
                <ioTune>
                    <device name="test-device-by-name">
                        <maximum>
                            <total_bytes_sec>200</total_bytes_sec>
                            <total_iops_sec>201</total_iops_sec>
                            <read_bytes_sec>202</read_bytes_sec>
                            <read_iops_sec>203</read_iops_sec>
                            <write_bytes_sec>204</write_bytes_sec>
                            <write_iops_sec>205</write_iops_sec>
                        </maximum>
                        <guaranteed>
                            <total_bytes_sec>100</total_bytes_sec>
                            <total_iops_sec>101</total_iops_sec>
                            <read_bytes_sec>102</read_bytes_sec>
                            <read_iops_sec>103</read_iops_sec>
                            <write_bytes_sec>104</write_bytes_sec>
                            <write_iops_sec>105</write_iops_sec>
                        </guaranteed>
                    </device>
                    <device path="test-device-by-path">
                        <maximum>
                            <total_bytes_sec>400</total_bytes_sec>
                            <total_iops_sec>401</total_iops_sec>
                            <read_bytes_sec>402</read_bytes_sec>
                            <read_iops_sec>403</read_iops_sec>
                            <write_bytes_sec>404</write_bytes_sec>
                            <write_iops_sec>405</write_iops_sec>
                        </maximum>
                        <guaranteed>
                            <total_bytes_sec>300</total_bytes_sec>
                            <total_iops_sec>301</total_iops_sec>
                            <read_bytes_sec>302</read_bytes_sec>
                            <write_bytes_sec>304</write_bytes_sec>
                            <write_iops_sec>305</write_iops_sec>
                        </guaranteed>
                    </device>
                </ioTune>
            </qos>
            """)

            self.assertEqual(expected_xml, self._xml_sanitizer(dom._metadata))

    def testIoTuneParser(self):
        with FakeVM() as machine:
            dom = FakeDomain()
            machine._dom = dom

            ioTuneValues = {
                "name": "test-device-by-name",
                "path": "test-path",
                "maximum": {
                    "total_bytes_sec": 200, "total_iops_sec": 201,
                    "read_bytes_sec": 202, "read_iops_sec": 203,
                    "write_bytes_sec": 204, "write_iops_sec": 205
                },
                "guaranteed": {
                    "total_bytes_sec": 100, "total_iops_sec": 101,
                    "read_bytes_sec": 102, "read_iops_sec": 103,
                    "write_bytes_sec": 104, "write_iops_sec": 105
                }
            }

            dom = ioTuneToDom(ioTuneValues)
            parsed = ioTuneDomToValues(dom)

            self.assertEqual(ioTuneValues, parsed)

    def testIoTuneMerge(self):
        with FakeVM() as machine:
            dom = FakeDomain()
            machine._dom = dom

            ioTuneValues1 = {
                "path": "test-path",
                "maximum": {
                    "total_bytes_sec": 0, "total_iops_sec": 0,
                    "read_bytes_sec": 0,
                    "write_bytes_sec": 999, "write_iops_sec": 0
                },
                "guaranteed": {
                    "total_bytes_sec": 999, "total_iops_sec": 0,
                    "read_bytes_sec": 0, "read_iops_sec": 0,
                    "write_bytes_sec": 0, "write_iops_sec": 0
                }
            }

            ioTuneValues2 = {
                "name": "test-device-by-name",
                "maximum": {
                    "total_bytes_sec": 200, "total_iops_sec": 201,
                    "read_bytes_sec": 202, "read_iops_sec": 203,
                    "write_iops_sec": 205
                },
                "guaranteed": {
                    "total_bytes_sec": -1, "total_iops_sec": 101,
                    "read_bytes_sec": 102, "read_iops_sec": 103,
                    "write_bytes_sec": 104, "write_iops_sec": 105
                }
            }

            ioTuneExpectedValues = {
                "name": "test-device-by-name",
                "path": "test-path",
                "maximum": {
                    "total_bytes_sec": 200, "total_iops_sec": 201,
                    "read_bytes_sec": 202, "read_iops_sec": 203,
                    "write_bytes_sec": 999, "write_iops_sec": 205
                },
                "guaranteed": {
                    "total_bytes_sec": -1, "total_iops_sec": 101,
                    "read_bytes_sec": 102, "read_iops_sec": 103,
                    "write_bytes_sec": 104, "write_iops_sec": 105
                }
            }

            ioTuneMerged = ioTuneMerge(ioTuneValues1, ioTuneValues2)

            self.assertEqual(ioTuneMerged, ioTuneExpectedValues)

    def testUpdateExistingVmPolicy(self):
        with FakeVM() as machine:
            dom = FakeDomain()
            dom._metadata = """
            <qos>
                <vcpuLimit>999</vcpuLimit>
                <ioTune>
                    <device name='test-device-by-name'>
                        <maximum>
                            <totalBytes>9999</totalBytes>
                        </maximum>
                    </device>
                    <device name='other-device'>
                        <maximum>
                            <totalBytes>9999</totalBytes>
                        </maximum>
                    </device>
                </ioTune>
            </qos>
            """

            machine._dom = dom

            policy = {
                "vcpuLimit": 50,
                "ioTune": [
                    {
                        "name": "test-device-by-name",
                        "maximum": {
                            "total_bytes_sec": 200, "total_iops_sec": 201,
                            "read_bytes_sec": 202, "read_iops_sec": 203,
                            "write_bytes_sec": 204, "write_iops_sec": 205
                        },
                        "guaranteed": {
                            "total_bytes_sec": 100, "total_iops_sec": 101,
                            "read_bytes_sec": 102, "read_iops_sec": 103,
                            "write_bytes_sec": 104, "write_iops_sec": 105
                        }
                    },
                    {
                        "path": "test-device-by-path",
                        "maximum": {
                            "total_bytes_sec": 400, "total_iops_sec": 401,
                            "read_bytes_sec": 402, "read_iops_sec": 403,
                            "write_bytes_sec": 404, "write_iops_sec": 405
                        },
                        "guaranteed": {
                            "total_bytes_sec": 300, "total_iops_sec": 301,
                            "read_bytes_sec": 302, "read_iops_sec": 303,
                            "write_bytes_sec": 304, "write_iops_sec": 305
                        }
                    }
                ]
            }

            machine.updateVmPolicy(policy)

            expected_xml = self._xml_sanitizer(u"""
            <qos>
                <ioTune>
                    <device name="other-device">
                        <maximum>
                            <totalBytes>9999</totalBytes>
                        </maximum>
                    </device>
                    <device name="test-device-by-name">
                        <maximum>
                            <total_bytes_sec>200</total_bytes_sec>
                            <total_iops_sec>201</total_iops_sec>
                            <read_bytes_sec>202</read_bytes_sec>
                            <read_iops_sec>203</read_iops_sec>
                            <write_bytes_sec>204</write_bytes_sec>
                            <write_iops_sec>205</write_iops_sec>
                        </maximum>
                        <guaranteed>
                            <total_bytes_sec>100</total_bytes_sec>
                            <total_iops_sec>101</total_iops_sec>
                            <read_bytes_sec>102</read_bytes_sec>
                            <read_iops_sec>103</read_iops_sec>
                            <write_bytes_sec>104</write_bytes_sec>
                            <write_iops_sec>105</write_iops_sec>
                        </guaranteed>
                    </device>
                    <device path="test-device-by-path">
                        <maximum>
                            <total_bytes_sec>400</total_bytes_sec>
                            <total_iops_sec>401</total_iops_sec>
                            <read_bytes_sec>402</read_bytes_sec>
                            <read_iops_sec>403</read_iops_sec>
                            <write_bytes_sec>404</write_bytes_sec>
                            <write_iops_sec>405</write_iops_sec>
                        </maximum>
                        <guaranteed>
                            <total_bytes_sec>300</total_bytes_sec>
                            <total_iops_sec>301</total_iops_sec>
                            <read_bytes_sec>302</read_bytes_sec>
                            <read_iops_sec>303</read_iops_sec>
                            <write_bytes_sec>304</write_bytes_sec>
                            <write_iops_sec>305</write_iops_sec>
                        </guaranteed>
                    </device>
                </ioTune>
                <vcpuLimit>50</vcpuLimit>
            </qos>
            """)

            self.maxDiff = None
            self.assertEqual(expected_xml, self._xml_sanitizer(dom._metadata))


class FakeGuestAgent(object):
    def __init__(self):
        self.guestDiskMapping = {}
        self.diskMappingHash = 0

    def getGuestInfo(self):
        return {
            'username': 'Unknown',
            'session': 'Unknown',
            'memUsage': 0,
            'appsList': [],
            'guestIPs': '',
            'guestFQDN': '',
            'disksUsage': [],
            'netIfaces': [],
            'memoryStats': {},
            'guestCPUCount': -1}


@contextmanager
def FakeVM(params=None, devices=None, runCpu=False,
           arch=caps.Architecture.X86_64, status=None):
    with namedTemporaryDir() as tmpDir:
        with MonkeyPatchScope([(constants, 'P_VDSM_RUN', tmpDir + '/'),
                               (libvirtconnection, 'get', ConnectionMock)]):
            vmParams = {'vmId': 'TESTING'}
            vmParams.update({} if params is None else params)
            cif = FakeClientIF()
            fake = vm.Vm(cif, vmParams)
            cif.vmContainer[fake.id] = fake
            fake.arch = arch
            fake.guestAgent = FakeGuestAgent()
            fake.conf['devices'] = [] if devices is None else devices
            fake._guestCpuRunning = runCpu
            if status is not None:
                fake._lastStatus = status
            yield fake


@expandPermutations
class TestVmOperations(TestCaseBase):
    # just numbers, no particular meaning
    UPDATE_OFFSETS = [-3200, 3502, -2700, 3601]
    BASE_OFFSET = 42

    GRAPHIC_DEVICES = [{'type': 'graphics', 'device': 'spice', 'port': '-1'},
                       {'type': 'graphics', 'device': 'vnc', 'port': '-1'}]

    @MonkeyPatch(libvirtconnection, 'get', lambda x: ConnectionMock())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetNotPresentByDefault(self, exitCode):
        with FakeVM() as fake:
            fake.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            self.assertFalse('timeOffset' in fake.getStats())

    @MonkeyPatch(libvirtconnection, 'get', lambda x: ConnectionMock())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetRoundtrip(self, exitCode):
        with FakeVM({'timeOffset': self.BASE_OFFSET}) as fake:
            fake.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            self.assertEqual(fake.getStats()['timeOffset'],
                             self.BASE_OFFSET)

    @MonkeyPatch(libvirtconnection, 'get', lambda x: ConnectionMock())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetRoundtriupAcrossInstances(self, exitCode):
        # bz956741
        lastOffset = 0
        for offset in self.UPDATE_OFFSETS:
            with FakeVM({'timeOffset': lastOffset}) as fake:
                fake._rtcUpdate(offset)
                fake.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
                vmOffset = fake.getStats()['timeOffset']
                self.assertEqual(vmOffset, str(lastOffset + offset))
                # the field in getStats is str, not int
                lastOffset = int(vmOffset)

    @MonkeyPatch(libvirtconnection, 'get', lambda x: ConnectionMock())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetUpdateIfAbsent(self, exitCode):
        # bz956741 (-like, simpler case)
        with FakeVM() as fake:
            for offset in self.UPDATE_OFFSETS:
                fake._rtcUpdate(offset)
            # beware of type change!
            fake.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            self.assertEqual(fake.getStats()['timeOffset'],
                             str(self.UPDATE_OFFSETS[-1]))

    @MonkeyPatch(libvirtconnection, 'get', lambda x: ConnectionMock())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetUpdateIfPresent(self, exitCode):
        with FakeVM({'timeOffset': self.BASE_OFFSET}) as fake:
            for offset in self.UPDATE_OFFSETS:
                fake._rtcUpdate(offset)
            # beware of type change!
            fake.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            self.assertEqual(fake.getStats()['timeOffset'],
                             str(self.BASE_OFFSET + self.UPDATE_OFFSETS[-1]))

    def testUpdateSingleDeviceGraphics(self):
        devXmls = (
            '<graphics connected="disconnect" passwd="***"'
            ' port="5900" type="spice"/>',
            '<graphics passwd="***" port="5900" type="vnc"/>')
        for device, devXml in zip(self.GRAPHIC_DEVICES, devXmls):
            domXml = '''
                <devices>
                    <graphics type="%s" port="5900" />
                </devices>''' % device['device']
            self._verifyDeviceUpdate(device, device, domXml, devXml)

    def testUpdateMultipleDeviceGraphics(self):
        devXmls = (
            '<graphics connected="disconnect" passwd="***"'
            ' port="5900" type="spice"/>',
            '<graphics passwd="***" port="5901" type="vnc"/>')
        domXml = '''
            <devices>
                <graphics type="spice" port="5900" />
                <graphics type="vnc" port="5901" />
            </devices>'''
        for device, devXml in zip(self.GRAPHIC_DEVICES, devXmls):
            self._verifyDeviceUpdate(
                device, self.GRAPHIC_DEVICES, domXml, devXml)

    def _verifyDeviceUpdate(self, device, allDevices, domXml, devXml):
        with FakeVM(devices=allDevices) as fake:
            fake._dom = FakeDomain(domXml)
            fake.updateDevice({
                'deviceType': 'graphics',
                'graphicsType': device['device'],
                'password': '***',
                'ttl': 0,
                'existingConnAction': 'disconnect'})
            self.assertEquals(fake._dom.devXml, devXml)

    def testDomainNotRunningWithoutDomain(self):
        with FakeVM() as fake:
            self.assertEqual(fake._dom, None)
            self.assertFalse(fake._isDomainRunning())

    def testDomainNotRunningByState(self):
        with FakeVM() as fake:
            fake._dom = FakeDomain(domState=libvirt.VIR_DOMAIN_SHUTDOWN)
            self.assertFalse(fake._isDomainRunning())

    def testDomainIsRunning(self):
        with FakeVM() as fake:
            fake._dom = FakeDomain(domState=libvirt.VIR_DOMAIN_RUNNING)
            self.assertTrue(fake._isDomainRunning())


VM_EXITS = tuple(product((define.NORMAL, define.ERROR),
                 vmexitreason.exitReasons.keys()))


@expandPermutations
class TestVmExit(TestCaseBase):
    @permutations(VM_EXITS)
    def testExitReason(self, exitCode, exitReason):
        """
        test of:
        exitReason round trip;
        error message is constructed correctly automatically
        """
        with FakeVM() as fake:
            fake.setDownStatus(exitCode, exitReason)
            stats = fake.getStats()
            self.assertEqual(stats['exitReason'], exitReason)
            self.assertEqual(stats['exitMessage'],
                             vmexitreason.exitReasons.get(exitReason))

    @permutations(VM_EXITS)
    def testExitReasonExplicitMessage(self, exitCode, exitReason):
        """
        test of:
        exitReason round trip;
        error message can be overridden explicitely
        """
        with FakeVM() as fake:
            msg = "test custom error message"
            fake.setDownStatus(exitCode, exitReason, msg)
            stats = fake.getStats()
            self.assertEqual(stats['exitReason'], exitReason)
            self.assertEqual(stats['exitMessage'], msg)


class TestVmStatsThread(TestCaseBase):
    VM_PARAMS = {'displayPort': -1, 'displaySecurePort': -1,
                 'display': 'qxl', 'displayIp': '127.0.0.1',
                 'vmType': 'kvm', 'memSize': 1024}

    DEV_BALLOON = [{'type': 'balloon', 'specParams': {'model': 'virtio'}}]

    def testGetNicStats(self):
        GBPS = 10 ** 9 / 8
        MAC = '52:54:00:59:F5:3F'
        with FakeVM() as fake:
            mock_stats_thread = vm.VmStatsThread(fake)
            res = mock_stats_thread._getNicStats(
                name='vnettest', model='virtio', mac=MAC,
                start_sample=(2 ** 64 - 15 * GBPS, 1, 2, 3, 0, 4, 5, 6),
                end_sample=(0, 7, 8, 9, 5 * GBPS, 10, 11, 12),
                interval=15.0)
            self.assertEqual(res, {
                'rxErrors': '8', 'rxDropped': '9',
                'txErrors': '11', 'txDropped': '12',
                'macAddr': MAC, 'name': 'vnettest',
                'speed': '1000', 'state': 'unknown',
                'rxRate': '100.0', 'txRate': '33.3'})

    def testGetStatsNoDom(self):
        # bz1073478 - main case
        with FakeVM(self.VM_PARAMS, self.DEV_BALLOON) as fake:
            self.assertEqual(fake._dom, None)
            mock_stats_thread = vm.VmStatsThread(fake)
            res = {}
            mock_stats_thread._getBalloonStats(res)
            self.assertIn('balloonInfo', res)
            self.assertIn('balloon_cur', res['balloonInfo'])

    def testGetStatsDomInfoFail(self):
        # bz1073478 - extra case
        with FakeVM(self.VM_PARAMS, self.DEV_BALLOON) as fake:
            fake._dom = FakeDomain(
                virtError=libvirt.VIR_ERR_NO_DOMAIN)
            mock_stats_thread = vm.VmStatsThread(fake)
            res = {}
            mock_stats_thread._getBalloonStats(res)
            self.assertIn('balloonInfo', res)
            self.assertIn('balloon_cur', res['balloonInfo'])

    def testMultipleGraphicDeviceStats(self):
        devices = [{'type': 'graphics', 'device': 'spice', 'port': '-1'},
                   {'type': 'graphics', 'device': 'vnc', 'port': '-1'}]

        with FakeVM(self.VM_PARAMS, devices) as fake:
            fake._updateDevices(fake.buildConfDevices())
            res = fake.getStats()
            self.assertIn('displayPort', res)
            self.assertEqual(res['displayType'],
                             'qxl' if devices[0]['device'] == 'spice' else
                             'vnc')
            for statsDev, confDev in zip(res['displayInfo'], devices):
                self.assertIn(statsDev['type'], confDev['device'])
                self.assertIn('port', statsDev)

    def testDiskMappingHashInStatsHash(self):
        with FakeVM(self.VM_PARAMS) as fake:
            res = fake.getStats()
            fake.guestAgent.diskMappingHash += 1
            self.assertNotEquals(res['hash'],
                                 fake.getStats()['hash'])


class TestLibVirtCallbacks(TestCaseBase):
    FAKE_ERROR = 'EFAKERROR'

    def test_onIOErrorPause(self):
        with FakeVM(runCpu=True) as fake:
            self.assertTrue(fake._guestCpuRunning)
            fake._onIOError('fakedev', self.FAKE_ERROR,
                            libvirt.VIR_DOMAIN_EVENT_IO_ERROR_PAUSE)
            self.assertFalse(fake._guestCpuRunning)
            self.assertEqual(fake.conf.get('pauseCode'), self.FAKE_ERROR)

    def test_onIOErrorReport(self):
        with FakeVM(runCpu=True) as fake:
            self.assertTrue(fake._guestCpuRunning)
            fake._onIOError('fakedev', self.FAKE_ERROR,
                            libvirt.VIR_DOMAIN_EVENT_IO_ERROR_REPORT)
            self.assertTrue(fake._guestCpuRunning)
            self.assertNotEquals(fake.conf.get('pauseCode'), self.FAKE_ERROR)

    def test_onIOErrorNotSupported(self):
        """action not explicitely handled, must be skipped"""
        with FakeVM(runCpu=True) as fake:
            self.assertTrue(fake._guestCpuRunning)
            fake._onIOError('fakedev', self.FAKE_ERROR,
                            libvirt.VIR_DOMAIN_EVENT_IO_ERROR_NONE)
            self.assertTrue(fake._guestCpuRunning)
            self.assertNotIn('pauseCode', fake.conf)  # no error recorded


@expandPermutations
class TestVmDevices(TestCaseBase):
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
            with FakeVM(conf) as fake:
                devs = fake.buildConfDevices()
                self.assertTrue(devs['graphics'])

    def testGraphicsDevice(self):
        for dev in self.confDeviceGraphics:
            with FakeVM(self.conf, dev) as fake:
                devs = fake.buildConfDevices()
                self.assertTrue(devs['graphics'])

    def testGraphicsDeviceMixed(self):
        """
        if proper Graphics Devices are supplied, display* params must be
        ignored.
        """
        for conf in self.confDisplay:
            conf.update(self.conf)
            for dev in self.confDeviceGraphics:
                with FakeVM(self.conf, dev) as fake:
                    devs = fake.buildConfDevices()
                    self.assertEqual(len(devs['graphics']), 1)
                    self.assertEqual(devs['graphics'][0]['device'],
                                     dev[0]['device'])

    def testGraphicsDeviceSanityLegacy(self):
        for conf in self.confDisplay:
            conf.update(self.conf)
            self.assertTrue(vm.GraphicsDevice.isSupportedDisplayType(conf))

    def testGraphicsDeviceSanity(self):
        for dev in self.confDeviceGraphics:
            conf = {'display': 'qxl', 'devices': list(dev)}
            conf.update(self.conf)
            self.assertTrue(vm.GraphicsDevice.isSupportedDisplayType(conf))

    def testGraphicDeviceUnsupported(self):
        conf = {'display': 'rdp'}
        conf.update(self.conf)
        self.assertFalse(vm.GraphicsDevice.isSupportedDisplayType(conf))

    def testHasSpiceLegacy(self):
        for conf in self.confDisplaySpice:
            conf.update(self.conf)
            with FakeVM(conf) as fake:
                self.assertTrue(fake.hasSpice)

        for conf in self.confDisplayVnc:
            conf.update(self.conf)
            with FakeVM(conf) as fake:
                self.assertFalse(fake.hasSpice)

    def testHasSpice(self):
        for dev in self.confDeviceGraphicsSpice:
            with FakeVM(self.conf, dev) as fake:
                self.assertTrue(fake.hasSpice)

        for dev in self.confDeviceGraphicsVnc:
            with FakeVM(self.conf, dev) as fake:
                self.assertFalse(fake.hasSpice)

    @permutations([['vnc', 'spice'], ['spice', 'vnc']])
    def testGraphicsDeviceMultiple(self, primary, secondary):
        devices = [{'type': 'graphics', 'device': primary},
                   {'type': 'graphics', 'device': secondary}]
        with FakeVM(self.conf, devices) as fake:
            devs = fake.buildConfDevices()
            self.assertTrue(len(devs['graphics']) == 2)

    @permutations([['vnc'], ['spice']])
    def testGraphicsDeviceDuplicated(self, devType):
        devices = [{'type': 'graphics', 'device': devType},
                   {'type': 'graphics', 'device': devType}]
        with FakeVM(self.conf, devices) as fake:
            self.assertRaises(ValueError, fake.buildConfDevices)


@expandPermutations
class TestVmFunctions(TestCaseBase):

    _CONFS = {
        caps.Architecture.X86_64: CONF_TO_DOMXML_X86_64,
        caps.Architecture.PPC64: CONF_TO_DOMXML_PPC64,
        'novdsm': CONF_TO_DOMXML_NO_VDSM}

    def _buildAllDomains(self, arch):
        for conf, _ in self._CONFS[arch]:
            with FakeVM(conf, arch=arch) as v:
                domXml = v._buildCmdLine()
                yield FakeDomain(domXml, vmId=v.id), domXml

    def _getAllDomains(self, arch):
        for conf, rawXml in self._CONFS[arch]:
            domXml = rawXml % conf
            yield FakeDomain(domXml, vmId=conf['vmId']), domXml

    def _getAllDomainIds(self, arch):
        return [conf['vmId'] for conf, _ in self._CONFS[arch]]

    @permutations([[caps.Architecture.X86_64], [caps.Architecture.PPC64]])
    def testGetVDSMDomains(self, arch):
        with MonkeyPatchScope([(vm, '_listDomains',
                                lambda: self._buildAllDomains(arch))]):
            self.assertEqual([v.UUIDString() for v in vm.getVDSMDomains()],
                             self._getAllDomainIds(arch))

    # VDSM (of course) builds correct config, so we need static examples
    # of incorrect/not-compliant data
    def testSkipNotVDSMDomains(self):
        with MonkeyPatchScope([(vm, '_listDomains',
                                lambda: self._getAllDomains('novdsm'))]):
            self.assertFalse(vm.getVDSMDomains())


class TestVmStatusTransitions(TestCaseBase):
    @slowtest
    def testSavingState(self):
        with FakeVM(runCpu=True, status=vmstatus.UP) as vm:
            vm._dom = FakeDomain(domState=libvirt.VIR_DOMAIN_RUNNING)

            def _asyncEvent():
                vm._onLibvirtLifecycleEvent(
                    libvirt.VIR_DOMAIN_EVENT_SUSPENDED,
                    -1, -1)

            t = threading.Thread(target=_asyncEvent)
            t.daemon = True

            def _fireAsyncEvent(*args):
                t.start()
                time.sleep(0.5)
                # pause the main thread to let the event one run

            with MonkeyPatchScope([(vm, '_underlyingPause', _fireAsyncEvent)]):
                self.assertEqual(vm.status()['status'], vmstatus.UP)
                vm.pause(vmstatus.SAVING_STATE)
                self.assertEqual(vm.status()['status'], vmstatus.SAVING_STATE)
                t.join()
                self.assertEqual(vm.status()['status'], vmstatus.SAVING_STATE)
                # state must not change even after we are sure the event was
                # handled
