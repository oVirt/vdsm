#
# Copyright IBM Corp. 2012
# Copyright 2013-2016 Red Hat, Inc.
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
from __future__ import absolute_import

from itertools import product
import logging
import os.path
import threading
import time
import uuid
import xml.etree.cElementTree as etree

import libvirt
import six
from six.moves import zip

from vdsm.virt import vmchannels
from vdsm.virt import vmexitreason
from vdsm.virt import vmstats
from vdsm.virt import vmstatus
from vdsm.virt import virdomain

from virt import vm
from virt.vm import HotunplugTimeout
from virt import vmdevices
from virt.vmdevices import hwclass
from virt.vmtune import io_tune_merge, io_tune_dom_to_values, io_tune_to_dom
from virt import vmxml
from virt.vmdevices.storage import Drive
from virt.vmdevices.storage import DISK_TYPE
from virt.vmdevices.network import Interface
from vdsm import constants
from vdsm import cpuarch
from vdsm.common import define
from vdsm.common import response
from vdsm import osinfo
from vdsm import password
from testlib import VdsmTestCase as TestCaseBase
from testlib import permutations, expandPermutations
from testlib import find_xml_element
from testlib import make_config
from testlib import recorded
from testlib import XMLTestCase
from vdsm import host
from vdsm import utils
from vdsm import libvirtconnection
from monkeypatch import MonkeyPatch, MonkeyPatchScope
from testlib import namedTemporaryDir
from testValidation import slowtest
from vmTestsData import CONF_TO_DOMXML_X86_64
from vmTestsData import CONF_TO_DOMXML_PPC64
import vmfakelib as fake


_VM_PARAMS = {
    'displayPort': -1,
    'displaySecurePort': -1,
    'display': 'qxl',
    'displayIp': '127.0.0.1',
    'vmType': 'kvm',
    'memSize': 1024
}


_TICKET_PARAMS = {
    'userName': 'admin',
    'userId': 'fdfc627c-d875-11e0-90f0-83df133b58cc'
}


_GRAPHICS_DEVICE_PARAMS = {
    'deviceType': hwclass.GRAPHICS,
    'password': password.ProtectedPassword('12345678'),
    'ttl': 0,
    'existingConnAction': 'disconnect',
    'params': _TICKET_PARAMS
}


@expandPermutations
class TestVm(XMLTestCase):

    def __init__(self, *args, **kwargs):
        TestCaseBase.__init__(self, *args, **kwargs)
        self.channelListener = None
        self.conf = {'vmName': 'testVm',
                     'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
                     'smp': '8', 'maxVCpus': '160',
                     'memSize': '1024', 'memGuaranteedSize': '512'}

    def assertBuildCmdLine(self, confToDom):
        with namedTemporaryDir() as tmpDir:
            with MonkeyPatchScope([(constants, 'P_VDSM_RUN', tmpDir + '/')]):
                for conf, expectedXML in confToDom:

                    expectedXML = expectedXML % conf

                    testVm = vm.Vm(self, conf)

                    output = testVm._buildDomainXML()

                    self.assertXMLEqual(output, expectedXML)

    def testDomXML(self):
        expectedXML = """
           <domain xmlns:ns0="http://ovirt.org/vm/tune/1.0" type="kvm">
              <name>testVm</name>
              <uuid>9ffe28b6-6134-4b1e-8804-1185f49c436f</uuid>
              <memory>1048576</memory>
              <currentMemory>1048576</currentMemory>
              <vcpu current="8">160</vcpu>
              <devices/>
              <metadata>
                 <ns0:qos/>
              </metadata>
           </domain>"""

        domxml = vmxml.Domain(self.conf, self.log, cpuarch.X86_64)
        self.assertXMLEqual(domxml.toxml(), expectedXML)

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
                 <bootmenu enable="yes" timeout="10000"/>
            </os>""", """
            <os>
                 <type arch="x86_64" machine="pc">hvm</type>
                 <smbios mode="sysinfo"/>
            </os>""", """
            <os>
                 <type arch="x86_64" machine="pc">hvm</type>
                 <smbios mode="sysinfo"/>
                 <bootmenu enable="yes" timeout="10000"/>
            </os>""", """
            <os>
                 <type arch="x86_64" machine="pc">hvm</type>
                 <smbios mode="sysinfo"/>
            </os>""", """
            <os>
                 <type arch="x86_64" machine="pc">hvm</type>
                 <cmdline>console=ttyS0 1</cmdline>
                 <smbios mode="sysinfo"/>
                 <bootmenu enable="yes" timeout="10000"/>
            </os>""", """
            <os>
                 <type arch="x86_64" machine="pc">hvm</type>
                 <cmdline>console=ttyS0 1</cmdline>
                 <smbios mode="sysinfo"/>
            </os>""")
        for conf, osXML in zip(vmConfs, expectedXMLs):
            conf.update(self.conf)
            domxml = vmxml.Domain(conf, self.log, cpuarch.X86_64)
            domxml.appendOs()
            xml = find_xml_element(domxml.toxml(), './os')
            self.assertXMLEqual(xml, osXML)

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
        for k, v in six.iteritems(qemu2libvirtBoot):
            vmConfs.append({'boot': k})
            expectedXMLs.append(OSXML % v)

        for vmConf, osXML in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = vmxml.Domain(vmConf, self.log, cpuarch.X86_64)
            domxml.appendOs()
            xml = find_xml_element(domxml.toxml(), './os')
            self.assertXMLEqual(xml, osXML)

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
        for k, v in six.iteritems(qemu2libvirtBoot):
            vmConfs.append({'boot': k})
            expectedXMLs.append(OSXML % v)

        for vmConf, osXML in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = vmxml.Domain(vmConf, self.log, cpuarch.PPC64)
            domxml.appendOs()
            xml = find_xml_element(domxml.toxml(), './os')
            self.assertXMLEqual(xml, osXML)

    def testFeaturesXML(self):
        featuresXML = """
            <features>
                  <acpi/>
            </features>"""
        domxml = vmxml.Domain(self.conf, self.log, cpuarch.X86_64)
        domxml.appendFeatures()
        xml = find_xml_element(domxml.toxml(), './features')
        self.assertXMLEqual(xml, featuresXML)

    def testFeaturesHyperVXML(self):
        featuresXML = """
            <features>
                  <acpi/>
                  <hyperv>
                         <relaxed state="on"/>
                         <vapic state="on"/>
                         <spinlocks retries="8191" state="on"/>
                  </hyperv>
            </features>"""
        conf = {'hypervEnable': 'true'}
        conf.update(self.conf)
        domxml = vmxml.Domain(conf, self.log, cpuarch.X86_64)
        domxml.appendFeatures()
        xml = find_xml_element(domxml.toxml(), './features')
        self.assertXMLEqual(xml, featuresXML)

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
        domxml = vmxml.Domain(self.conf, self.log, cpuarch.X86_64)
        domxml.appendSysinfo(product, version, serial)
        xml = find_xml_element(domxml.toxml(), './sysinfo')
        self.assertXMLEqual(xml, sysinfoXML)

    @permutations([
        # console_type, cpu_arch, use_serial, check_attrib
        ['serial', cpuarch.X86_64, True, True],
        ['virtio', cpuarch.X86_64, False, True],
        ['serial', cpuarch.PPC64, False, False],
        ['serial', cpuarch.PPC64LE, False, False],
    ])
    def testSerialBios(self, console_type, cpu_arch, use_serial, check_attrib):
        devices = {'device': 'console', 'type': 'console',
                   'specParams': {'consoleType': console_type}},
        with fake.VM(devices=devices, arch=cpu_arch,
                     create_device_objects=True) as fakevm:
            dom_xml = fakevm._buildDomainXML()
            tree = etree.fromstring(dom_xml)
            xpath = ".//bios"
            if check_attrib:
                xpath += "[@useserial='yes']"
            element = tree.find(xpath)
            self.assertEqual(element is not None, use_serial)

    def testConsoleXMLVirtio(self):
        consoleXML = """
            <console type="pty">
                <target port="0" type="virtio"/>
            </console>"""
        dev = {'device': 'console', 'specParams': {'consoleType': 'virtio'}}
        console = vmdevices.core.Console(self.conf, self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(console.getXML()), consoleXML)

    def testConsoleXMLSerial(self):
        consoleXML = """
            <console type="pty">
                <target port="0" type="serial"/>
            </console>"""
        dev = {'device': 'console', 'specParams': {'consoleType': 'serial'}}
        console = vmdevices.core.Console(self.conf, self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(console.getXML()), consoleXML)

    def testConsoleXMLDefault(self):
        consoleXML = """
            <console type="pty">
                <target port="0" type="virtio"/>
            </console>"""
        dev = {'device': 'console'}
        console = vmdevices.core.Console(self.conf, self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(console.getXML()), consoleXML)

    def testSerialDeviceXML(self):
        serialXML = """
            <serial type="pty">
                <target port="0"/>
            </serial>"""
        dev = {'device': 'console'}
        console = vmdevices.core.Console(self.conf, self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(console.getSerialDeviceXML()),
                            serialXML)

    def testUnixSocketSerialDeviceXML(self):
        path = "/var/run/ovirt-vmconsole-console/%s.sock" % self.conf['vmId']
        serialXML = """
            <serial type="unix">
                <source mode="bind" path="%s" />
                <target port="0" />
            </serial>""" % path
        dev = {'device': 'console', 'specParams': {'enableSocket': True}}
        console = vmdevices.core.Console(self.conf, self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(console.getSerialDeviceXML()),
                            serialXML)

    def testClockXML(self):
        clockXML = """
            <clock adjustment="-3600" offset="variable">
                <timer name="rtc" tickpolicy="catchup"/>
                <timer name="pit" tickpolicy="delay"/>
                <timer name="hpet" present="no"/>
            </clock>"""
        self.conf['timeOffset'] = '-3600'
        domxml = vmxml.Domain(self.conf, self.log, cpuarch.X86_64)
        domxml.appendClock()
        xml = find_xml_element(domxml.toxml(), './clock')
        self.assertXMLEqual(xml, clockXML)

    def testHyperVClockXML(self):
        clockXML = """
            <clock adjustment="-3600" offset="variable">
                <timer name="hypervclock" present="yes"/>
                <timer name="rtc" tickpolicy="catchup"/>
                <timer name="pit" tickpolicy="delay"/>
                <timer name="hpet" present="no"/>
            </clock>"""
        conf = {'timeOffset': '-3600', 'hypervEnable': 'true'}
        conf.update(self.conf)
        domxml = vmxml.Domain(conf, self.log, cpuarch.X86_64)
        domxml.appendClock()
        xml = find_xml_element(domxml.toxml(), './clock')
        self.assertXMLEqual(xml, clockXML)

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
              <memnode cellid="0" mode="strict" nodeset="1"/>
              <memnode cellid="1" mode="strict" nodeset="0"/>
          </numatune> """

        vmConf = {'cpuType': "Opteron_G4,+sse4_1,+sse4_2,-svm",
                  'smpCoresPerSocket': 2, 'smpThreadsPerCore': 2,
                  'cpuPinning': {'0': '0-1', '1': '2-3'},
                  'numaTune': {'mode': 'strict',
                               'nodeset': '0-1',
                               'memnodes': [
                                   {'vmNodeIndex': '0', 'nodeset': '1'},
                                   {'vmNodeIndex': '1', 'nodeset': '0'}
                               ]},
                  'guestNumaNodes': [{'cpus': '0-1', 'memory': '5120',
                                      'nodeIndex': 0},
                                     {'cpus': '2,3', 'memory': '5120',
                                      'nodeIndex': 1}]}
        vmConf.update(self.conf)
        domxml = vmxml.Domain(vmConf, self.log, cpuarch.X86_64)
        domxml.appendCpu()
        domxml.appendNumaTune()
        xml = domxml.toxml()
        self.assertXMLEqual(find_xml_element(xml, "./cpu"), cpuXML)
        self.assertXMLEqual(find_xml_element(xml, "./cputune"), cputuneXML)
        self.assertXMLEqual(find_xml_element(xml, './numatune'), numatuneXML)

    def testChannelXML(self):
        channelXML = """
          <channel type="unix">
             <target name="%s" type="virtio"/>
             <source mode="bind" path="%s"/>
          </channel>"""
        path = '/tmp/channel-socket'
        name = 'org.linux-kvm.port.0'
        channelXML = channelXML % (name, path)
        domxml = vmxml.Domain(self.conf, self.log, cpuarch.X86_64)
        domxml._appendAgentDevice(path, name)
        xml = find_xml_element(domxml.toxml(), './devices/channel')
        self.assertXMLEqual(xml, channelXML)

    def testInputXMLX86_64(self):
        expectedXMLs = [
            """<input bus="ps2" type="mouse"/>""",
            """<input bus="usb" type="tablet"/>"""]

        vmConfs = [{}, {'tabletEnable': 'true'}]
        for vmConf, inputXML in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = vmxml.Domain(vmConf, self.log, cpuarch.X86_64)
            domxml.appendInput()
            xml = find_xml_element(domxml.toxml(), './devices/input')
            self.assertXMLEqual(xml, inputXML)

    def testInputXMLPPC64(self):
        expectedXMLs = [
            """<input bus="usb" type="mouse"/>""",
            """<input bus="usb" type="tablet"/>"""]

        vmConfs = [{}, {'tabletEnable': 'true'}]
        for vmConf, inputXML in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = vmxml.Domain(vmConf, self.log, cpuarch.PPC64)
            domxml.appendInput()
            xml = find_xml_element(domxml.toxml(), './devices/input')
            self.assertXMLEqual(xml, inputXML)

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
            drive = vmdevices.storage.Drive(vmConf, self.log,
                                            diskType=DISK_TYPE.FILE, **devConf)

            with self.assertRaises(Exception) as cm:
                drive.getXML()

            self.assertEquals(cm.exception.args[0], exceptionMsg)

    @MonkeyPatch(cpuarch, 'effective', lambda: cpuarch.X86_64)
    @MonkeyPatch(osinfo, 'version', lambda: {
        'release': '1', 'version': '18', 'name': 'Fedora'})
    @MonkeyPatch(constants, 'SMBIOS_MANUFACTURER', 'oVirt')
    @MonkeyPatch(constants, 'SMBIOS_OSNAME', 'oVirt Node')
    @MonkeyPatch(libvirtconnection, 'get', fake.Connection)
    @MonkeyPatch(host, 'uuid',
                 lambda: "fc25cbbe-5520-4f83-b82e-1541914753d9")
    @MonkeyPatch(vm.Vm, 'send_status_event', lambda x: None)
    def testBuildCmdLineX86_64(self):
        self.assertBuildCmdLine(CONF_TO_DOMXML_X86_64)

    @MonkeyPatch(cpuarch, 'effective', lambda: cpuarch.PPC64)
    @MonkeyPatch(osinfo, 'version', lambda: {
        'release': '1', 'version': '18', 'name': 'Fedora'})
    @MonkeyPatch(libvirtconnection, 'get', fake.Connection)
    @MonkeyPatch(host, 'uuid',
                 lambda: "fc25cbbe-5520-4f83-b82e-1541914753d9")
    @MonkeyPatch(vm.Vm, 'send_status_event', lambda x: None)
    def testBuildCmdLinePPC64(self):
        self.assertBuildCmdLine(CONF_TO_DOMXML_PPC64)

    def testVmPolicyOnStartup(self):
        LIMIT = '50'
        with fake.VM(_VM_PARAMS) as testvm:
            dom = fake.Domain()
            dom.setMetadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                            '<qos><vcpuLimit>%s</vcpuLimit></qos>' % (
                                LIMIT
                            ),
                            vmxml.METADATA_VM_TUNE_PREFIX,
                            vmxml.METADATA_VM_TUNE_URI,
                            0)
            testvm._dom = dom
            # it is bad practice to test private functions -and we know it.
            # But enduring the full VM startup is too cumbersome, and we
            # need to test this code.
            testvm._updateVcpuLimit()
            stats = testvm.getStats()
            self.assertEqual(stats['vcpuUserLimit'], LIMIT)

    def testGetVmPolicySucceded(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            self.assertXMLEqual(vmxml.format_xml(testvm._getVmPolicy()),
                                '<qos/>')

    def testGetVmPolicyEmptyOnNoMetadata(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(
                virtError=libvirt.VIR_ERR_NO_DOMAIN_METADATA)
            self.assertXMLEqual(vmxml.format_xml(testvm._getVmPolicy()),
                                '<qos/>')

    def testGetVmPolicyFailOnNoDomain(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(virtError=libvirt.VIR_ERR_NO_DOMAIN)
            self.assertEqual(testvm._getVmPolicy(), None)

    def testUpdateVmPolicy(self):
        with fake.VM() as machine:
            dom = fake.Domain()
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

            expected_xml = (u"""
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

            self.assertXMLEqual(expected_xml, dom._metadata)

    def testCpuTune(self):
        LIMIT = 50
        with fake.VM(_VM_PARAMS) as machine:
            machine._dom = fake.Domain()
            policy = {"vcpuLimit": LIMIT}

            machine.updateVmPolicy(policy)

            stats = machine.getStats()
            self.assertEqual(stats['vcpuUserLimit'], LIMIT)

    def testIoTuneParser(self):
        with fake.VM() as machine:
            dom = fake.Domain()
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

            dom = io_tune_to_dom(ioTuneValues)
            parsed = io_tune_dom_to_values(dom)

            self.assertEqual(ioTuneValues, parsed)

    def testIoTuneMerge(self):
        with fake.VM() as machine:
            dom = fake.Domain()
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

            ioTuneMerged = io_tune_merge(ioTuneValues1, ioTuneValues2)

            self.assertEqual(ioTuneMerged, ioTuneExpectedValues)

    def testUpdateExistingVmPolicy(self):
        with fake.VM() as machine:
            dom = fake.Domain()
            dom._metadata = """
            <qos>
                <vcpuLimit>999</vcpuLimit>
                <ioTune>
                    <device name='test-device-by-name'>
                        <maximum>
                            <total_bytes_sec>9999</total_bytes_sec>
                        </maximum>
                    </device>
                    <device name='other-device'>
                        <maximum>
                            <total_bytes_sec>9999</total_bytes_sec>
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

            expected_xml = (u"""
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
                    <device name="other-device">
                        <maximum>
                            <total_bytes_sec>9999</total_bytes_sec>
                        </maximum>
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
            </qos>
            """)

            self.assertXMLEqual(expected_xml, dom._metadata)

    def testGetIoTunePolicy(self):
        with fake.VM() as machine:
            dom = fake.Domain()
            dom._metadata = """
            <qos>
                <vcpuLimit>999</vcpuLimit>
                <ioTune>
                    <device name='test-device-by-name'>
                        <maximum>
                            <total_bytes_sec>9999</total_bytes_sec>
                        </maximum>
                    </device>
                    <device name='other-device'>
                        <guaranteed>
                            <total_bytes_sec>9999</total_bytes_sec>
                        </guaranteed>
                    </device>
                </ioTune>
            </qos>
            """
            machine._dom = dom
            machine._updateIoTuneInfo()

            tunables = machine.getIoTunePolicyResponse()
            expected = [
                {'name': u'test-device-by-name',
                 'maximum': {
                     u'total_bytes_sec': 9999
                 }},
                {'name': u'other-device',
                 'guaranteed': {
                     u'total_bytes_sec': 9999
                 }}
            ]
            self.assertEqual(tunables['ioTunePolicyList'], expected)

    @permutations([['<empty/>'], [None]])
    def testNoIoTunePolicy(self, metadata):
        with fake.VM() as machine:
            dom = fake.Domain()
            dom._metadata = metadata
            machine._dom = dom

            tunables = machine.getIoTunePolicyResponse()
            self.assertEqual(tunables['ioTunePolicyList'], [])

    def testSetIoTune(self):

        drives = [
            vmdevices.storage.Drive({
                "specParams": {
                    "ioTune": {
                        "total_bytes_sec": 9999,
                        "total_iops_sec": 9999}
                }},
                log=self.log,
                index=0,
                device="hdd",
                path="/dev/dummy",
                type=hwclass.DISK,
                iface="ide",
                diskType=DISK_TYPE.BLOCK
            )
        ]

        # Make the drive look like a VDSM volume
        required = ('domainID', 'imageID', 'poolID', 'volumeID')
        for p in required:
            setattr(drives[0], p, "1")

        tunables = [
            {
                "name": drives[0].name,
                "ioTune": {
                    "write_bytes_sec": 1,
                    "total_bytes_sec": 0,
                    "read_bytes_sec": 2
                }
            }
        ]

        expected_io_tune = {
            drives[0].name: {
                "write_bytes_sec": 1,
                "total_bytes_sec": 0,
                "read_bytes_sec": 2
            }
        }

        expected_xml = """
            <disk device="hdd" snapshot="no" type="block">
                <source dev="/dev/dummy"/>
                <target bus="ide" dev="hda"/>
                <iotune>
                    <read_bytes_sec>2</read_bytes_sec>
                    <write_bytes_sec>1</write_bytes_sec>
                    <total_bytes_sec>0</total_bytes_sec>
                </iotune>
            </disk>"""

        with fake.VM() as machine:
            dom = fake.Domain()
            machine._dom = dom
            for drive in drives:
                machine._devices[drive.type].append(drive)

            machine.setIoTune(tunables)

            self.assertEqual(expected_io_tune, dom._io_tune)

            # Test that caches were properly updated
            self.assertEqual(drives[0].specParams["ioTune"],
                             expected_io_tune[drives[0].name])
            self.assertXMLEqual(drives[0]._deviceXML, expected_xml)

    def testGetPolicyDisconnected(self):
        with fake.VM() as machine:
            machine._dom = virdomain.Disconnected(machine.id)
            policy = machine._getVmPolicy()
            self.assertEqual(policy, None)

    def testSdIds(self):
        """
        Tests that VM storage domains in use list is in sync with the vm
        devices in use
        """
        domainID = uuid.uuid4()
        drives = [
            vmdevices.storage.Drive(
                {
                    "specParams": {
                        "ioTune": {
                            "total_bytes_sec": 9999,
                            "total_iops_sec": 9999
                        }
                    }
                },
                log=self.log,
                index=0,
                device="disk",
                path="/dev/dummy",
                type=hwclass.DISK,
                iface="ide",
                domainID=domainID,
                imageID=uuid.uuid4(),
                poolID=uuid.uuid4(),
                volumeID=uuid.uuid4(),
                diskType=DISK_TYPE.BLOCK,
            ),
            vmdevices.storage.Drive(
                {
                    "specParams": {
                        "ioTune": {
                            "total_bytes_sec": 9999,
                            "total_iops_sec": 9999
                        }
                    }
                },
                log=self.log,
                index=0,
                device="hdd2",
                path="/dev/dummy2",
                type=hwclass.DISK,
                iface="ide",
                diskType=DISK_TYPE.BLOCK,
            )
        ]

        with fake.VM() as machine:
            for drive in drives:
                machine._devices[drive.type].append(drive)

            self.assertEqual(machine.sdIds, set([domainID]))

    def testVmGuestSocketFile(self):
        with fake.VM(self.conf) as testvm:
            self.assertEqual(
                testvm._guestSocketFile,
                testvm._makeChannelPath(vmchannels.DEVICE_NAME))

    def test_spice_restore_set_passwd(self):
        # stolen from VDSM logs
        conf = {
            'tlsPort': u'5901',
            u'specParams': {
                u'fileTransferEnable': u'true',
                u'copyPasteEnable': u'true',
                'displayIp': '0'
            },
            'deviceType': u'graphics',
            u'deviceId': u'dbb9db4e-1a30-45b7-b76d-608afc797be9',
            u'device': u'spice',
            u'type': u'graphics',
            'port': u'5900'
        }

        with fake.VM(devices=[conf], create_device_objects=True) as testvm:
            out_dom_xml = testvm._correctGraphicsConfiguration(
                _load_xml('vm_restore_spice_before.xml'))

        self.assertXMLEqual(out_dom_xml,
                            _load_xml('vm_restore_spice_after.xml'))

    @MonkeyPatch(os, 'unlink', lambda _: None)
    def test_release_vm_succeeds(self):
        with fake.VM(self.conf) as testvm:
            testvm.guestAgent = fake.GuestAgent()

            dom = fake.Domain()

            status = {
                'graceful': 0,
                'forceful': 0,
            }

            def graceful(*args):
                status['graceful'] += 1
                return response.success()

            def forceful(*args):
                status['forceful'] += 1
                return response.success()

            dom.destroyFlags = graceful
            dom.destroy = forceful
            testvm._dom = dom

            testvm.releaseVm()
            self.assertEqual(status, {
                'graceful': 1,
                'forceful': 0,
            })

    @MonkeyPatch(os, 'unlink', lambda _: None)
    @permutations([[1], [2], [3], [9]])
    def test_releasevm_fails(self, attempts):
        with fake.VM(self.conf) as testvm:
            testvm.guestAgent = fake.GuestAgent()

            dom = fake.Domain()

            status = {
                'graceful': 0,
                'forceful': 0,
            }

            def graceful(*args):
                status['graceful'] += 1
                raise fake.Error(libvirt.VIR_ERR_SYSTEM_ERROR)

            def forceful(*args):
                status['forceful'] += 1
                return response.success()

            dom.destroyFlags = graceful
            dom.destroy = forceful
            testvm._dom = dom

            testvm.releaseVm(gracefulAttempts=attempts)
            self.assertEqual(status, {
                'graceful': attempts,
                'forceful': 1,
            })


class ExpectedError(Exception):
    pass


class UnexpectedError(Exception):
    pass


class TestVmDeviceHandling(TestCaseBase):
    conf = {
        'devices': [],
        'maxVCpus': '160',
        'memGuaranteedSize': '512',
        'memSize': '1024',
        'smp': '8',
        'vmId': '9ffe28b6-6134-4b1e-beef-1185f49c436f',
        'vmName': 'testVm',
    }

    def test_device_setup_success(self):
        devices = [fake.Device('device_{}'.format(i)) for i in range(3)]

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices[hwclass.GENERAL] = devices
            self.assertNotRaises(testvm._setup_devices)
            self.assertEqual(devices[0].state, fake.SETUP)
            self.assertEqual(devices[1].state, fake.SETUP)
            self.assertEqual(devices[2].state, fake.SETUP)

    def test_device_setup_fail_first(self):
        devices = ([fake.Device('device_0', fail_setup=ExpectedError)] +
                   [fake.Device('device_{}'.format(i)) for i in range(1, 3)])

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices[hwclass.GENERAL] = devices
            self.assertRaises(ExpectedError, testvm._setup_devices)
            self.assertEqual(devices[0].state, fake.SETUP)
            self.assertEqual(devices[1].state, fake.CREATED)
            self.assertEqual(devices[2].state, fake.CREATED)

    def test_device_setup_fail_second(self):
        devices = [fake.Device('device_0'),
                   fake.Device('device_1', fail_setup=ExpectedError),
                   fake.Device('device_2')]

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices[hwclass.GENERAL] = devices
            self.assertRaises(ExpectedError, testvm._setup_devices)
            self.assertEqual(devices[0].state, fake.TEARDOWN)
            self.assertEqual(devices[1].state, fake.SETUP)
            self.assertEqual(devices[2].state, fake.CREATED)

    def test_device_setup_fail_third(self):
        devices = [fake.Device('device_0'), fake.Device('device_1'),
                   fake.Device('device_2', fail_setup=ExpectedError)]

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices[hwclass.GENERAL] = devices
            self.assertRaises(ExpectedError, testvm._setup_devices)
            self.assertEqual(devices[0].state, fake.TEARDOWN)
            self.assertEqual(devices[1].state, fake.TEARDOWN)
            self.assertEqual(devices[2].state, fake.SETUP)

    def test_device_setup_correct_exception(self):
        devices = [fake.Device('device_0', fail_teardown=UnexpectedError),
                   fake.Device('device_1',
                               fail_setup=ExpectedError,
                               fail_teardown=UnexpectedError),
                   fake.Device('device_2', fail_setup=UnexpectedError)]

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices[hwclass.GENERAL] = devices
            self.assertRaises(ExpectedError, testvm._setup_devices)
            self.assertEqual(devices[0].state, fake.TEARDOWN)
            self.assertEqual(devices[1].state, fake.SETUP)
            self.assertEqual(devices[2].state, fake.CREATED)

    def test_device_teardown_success(self):
        devices = [fake.Device('device_{}'.format(i)) for i in range(3)]

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices[hwclass.GENERAL] = devices
            self.assertNotRaises(testvm._setup_devices)
            self.assertNotRaises(testvm._teardown_devices)
            self.assertEqual(devices[0].state, fake.TEARDOWN)
            self.assertEqual(devices[1].state, fake.TEARDOWN)
            self.assertEqual(devices[2].state, fake.TEARDOWN)

    def test_device_teardown_fail_all(self):
        devices = [fake.Device('device_{}'.format(i),
                               fail_teardown=UnexpectedError)
                   for i in range(3)]

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices[hwclass.GENERAL] = devices
            self.assertNotRaises(testvm._setup_devices)
            self.assertNotRaises(testvm._teardown_devices)
            self.assertEqual(devices[0].state, fake.TEARDOWN)
            self.assertEqual(devices[1].state, fake.TEARDOWN)
            self.assertEqual(devices[2].state, fake.TEARDOWN)


@expandPermutations
class TestWaitForRemoval(TestCaseBase):

    FILE_DRIVE_XML = """
    <disk>
        <source file='test_path'/>
    </disk>"""
    NETWORK_DRIVE_XML = """
    <disk>
        <source name='test_path'/>
    </disk>"""
    BLOCK_DRIVE_XML = """
    <disk>
        <source dev='/block_path'/>
    </disk>"""

    NIC_XML = """
    <interface>
      <mac address='macAddr'/>
    </interface>
    """

    drive_file = Drive({}, log=logging.getLogger(''), index=0, iface="",
                       path='test_path', diskType=DISK_TYPE.FILE)
    drive_network = Drive({}, log=logging.getLogger(''), index=0, iface="",
                          path='test_path', diskType=DISK_TYPE.NETWORK)
    drive_block = Drive({}, log=logging.getLogger(''), index=0, iface="",
                        path="/block_path", diskType=DISK_TYPE.BLOCK)
    interface = Interface({}, log=logging.getLogger(''), macAddr="macAddr",
                          device='bridge', name='')

    @MonkeyPatch(vm, "config", make_config([
        ("vars", "hotunplug_timeout", "0.25"),
        ("vars", "hotunplug_check_interval", "0.1")
    ]))
    @MonkeyPatch(utils, "isBlockDevice", lambda x: x == "/block_path")
    @permutations([[drive_file, FILE_DRIVE_XML],
                   [drive_network, NETWORK_DRIVE_XML],
                   [drive_block, BLOCK_DRIVE_XML],
                   [interface, NIC_XML]])
    def test_timeout(self, device, matching_xml):
        testvm = TestingVm(WaitForRemovalFakeVmDom(matching_xml,
                                                   times_to_match=9))
        self.assertRaises(HotunplugTimeout, testvm._waitForDeviceRemoval,
                          device)

    # The timeout hotunplug_check_interval=1 should never be reached. We should
    # never reach sleep when device is removed in first check, and method
    # should exit immediately
    @MonkeyPatch(vm, "config", make_config([
        ("vars", "hotunplug_timeout", "1")
    ]))
    @MonkeyPatch(utils, "isBlockDevice", lambda x: x == "/block_path")
    @permutations([[drive_file, FILE_DRIVE_XML],
                   [drive_network, NETWORK_DRIVE_XML],
                   [drive_block, BLOCK_DRIVE_XML],
                   [interface, NIC_XML]])
    def test_removed_on_first_check(self, device, matching_xml):
        testvm = TestingVm(WaitForRemovalFakeVmDom(matching_xml))
        testvm._waitForDeviceRemoval(device)
        self.assertEqual(testvm._dom.xmldesc_fetched, 1)

    @MonkeyPatch(vm, "config", make_config([
        ("vars", "hotunplug_timeout", "1"),
        ("vars", "hotunplug_check_interval", "0")
    ]))
    @MonkeyPatch(utils, "isBlockDevice", lambda x: x == "/block_path")
    @permutations([[drive_file, FILE_DRIVE_XML],
                   [drive_network, NETWORK_DRIVE_XML],
                   [drive_block, BLOCK_DRIVE_XML],
                   [interface, NIC_XML]])
    def test_removed_on_x_check(self, device, matching_xml):
        testvm = TestingVm(WaitForRemovalFakeVmDom(matching_xml,
                                                   times_to_match=2))
        testvm._waitForDeviceRemoval(device)
        self.assertEqual(testvm._dom.xmldesc_fetched, 3)


class WaitForRemovalFakeVmDom(object):

    DEFAULT_XML = """
    <domain type='kvm' id='2'>
        <devices>
            <disk device="disk" snapshot="no" type="file">
                <source file="/path/to/volume"/>
                <target bus="virtio" dev="vda"/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="none" error_policy="stop"
                        io="threads" name="qemu" type="raw"/>
            </disk>
            <disk device="lun" sgio="unfiltered" snapshot="no" type="block">
                <source dev="/dev/mapper/lun1"/>
                <target bus="scsi" dev="sda"/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="raw"/>
            </disk>
            <disk device="cdrom" snapshot="no" type="file">
                <source file="/path/to/fedora.iso" startupPolicy="optional"/>
                <target bus="ide" dev="hdc"/>
                <readonly/>
                <serial>54-a672-23e5b495a9ea</serial>
            </disk>
        </devices>
    </domain>
    """

    def __init__(self, device_xml, times_to_match=0):
        self.times_to_match = times_to_match
        result_xml = etree.fromstring(self.DEFAULT_XML)
        result_xml.find("devices").append(etree.fromstring(device_xml))
        self.domain_xml_with_device = etree.tostring(result_xml)
        self.xmldesc_fetched = 0

    def XMLDesc(self, flags):
        self.xmldesc_fetched += 1
        if self.xmldesc_fetched > self.times_to_match:
            return self.DEFAULT_XML
        else:
            return self.domain_xml_with_device


VM_EXITS = tuple(product((define.NORMAL, define.ERROR),
                 list(vmexitreason.exitReasons.keys())))


@expandPermutations
class TestVmExit(TestCaseBase):
    @permutations(VM_EXITS)
    def testExitReason(self, exitCode, exitReason):
        """
        test of:
        exitReason round trip;
        error message is constructed correctly automatically
        """
        with fake.VM() as testvm:
            testvm.setDownStatus(exitCode, exitReason)
            stats = testvm.getStats()
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
        with fake.VM() as testvm:
            msg = "test custom error message"
            testvm.setDownStatus(exitCode, exitReason, msg)
            stats = testvm.getStats()
            self.assertEqual(stats['exitReason'], exitReason)
            self.assertEqual(stats['exitMessage'], msg)


_VM_PARAMS = {'displayPort': -1, 'displaySecurePort': -1,
              'display': 'qxl', 'displayIp': '127.0.0.1',
              'vmType': 'kvm', 'memSize': 1024}


class TestVmStats(TestCaseBase):

    DEV_BALLOON = [{'type': 'balloon', 'specParams': {'model': 'virtio'}}]

    def testGetNicStats(self):
        GBPS = 10 ** 9 / 8
        MAC = '52:54:00:59:F5:3F'
        pretime = utils.monotonic_time()
        with fake.VM(_VM_PARAMS) as testvm:
            res = vmstats._nic_traffic(
                testvm,
                name='vnettest', model='virtio', mac=MAC,
                start_sample={'net.0.rx.bytes': 2 ** 64 - 15 * GBPS,
                              'net.0.rx.pkts': 1,
                              'net.0.rx.errs': 2,
                              'net.0.rx.drop': 3,
                              'net.0.tx.bytes': 0,
                              'net.0.tx.pkts': 4,
                              'net.0.tx.errs': 5,
                              'net.0.tx.drop': 6},
                start_index=0,
                end_sample={'net.0.rx.bytes': 0,
                            'net.0.rx.pkts': 7,
                            'net.0.rx.errs': 8,
                            'net.0.rx.drop': 9,
                            'net.0.tx.bytes': 5 * GBPS,
                            'net.0.tx.pkts': 10,
                            'net.0.tx.errs': 11,
                            'net.0.tx.drop': 12},
                end_index=0,
                interval=15.0)
        posttime = utils.monotonic_time()
        self.assertIn('sampleTime', res)
        self.assertTrue(pretime <= res['sampleTime'] <= posttime,
                        'sampleTime not in [%s..%s]' % (pretime, posttime))
        del res['sampleTime']
        self.assertEqual(res, {
            'rxErrors': '8', 'rxDropped': '9',
            'txErrors': '11', 'txDropped': '12',
            'macAddr': MAC, 'name': 'vnettest',
            'speed': '1000', 'state': 'unknown',
            'rx': '0', 'tx': '625000000',
        })

    def testMultipleGraphicDeviceStats(self):
        devices = [{'type': 'graphics', 'device': 'spice', 'port': '-1'},
                   {'type': 'graphics', 'device': 'vnc', 'port': '-1'}]

        with fake.VM(_VM_PARAMS, devices) as testvm:
            dev_spec_map = testvm._devSpecMapFromConf()
            testvm._updateDevices(dev_spec_map)
            testvm._devices = testvm._devMapFromDevSpecMap(dev_spec_map)
            res = testvm.getStats()
            self.assertIn('displayPort', res)
            self.assertEqual(res['displayType'],
                             'qxl' if devices[0]['device'] == 'spice' else
                             'vnc')
            self.assertTrue(res['displayInfo'])
            for statsDev, confDev in zip(res['displayInfo'], devices):
                self.assertIn(statsDev['type'], confDev['device'])
                self.assertIn('port', statsDev)

    def testDiskMappingHashInStatsHash(self):
        with fake.VM(_VM_PARAMS) as testvm:
            res = testvm.getStats()
            testvm.guestAgent.diskMappingHash += 1
            self.assertNotEquals(res['hash'],
                                 testvm.getStats()['hash'])

    @MonkeyPatch(vm, 'config',
                 make_config([('vars', 'vm_command_timeout', '10')]))
    def testMonitorTimeoutResponsive(self):
        with fake.VM(_VM_PARAMS) as testvm:
            self.assertFalse(testvm.isMigrating())
            stats = {'monitorResponse': '0'}
            testvm._setUnresponsiveIfTimeout(stats, 1)  # any value < timeout
            self.assertEqual(stats['monitorResponse'], '0')

    @MonkeyPatch(vm, 'config',
                 make_config([('vars', 'vm_command_timeout', '1')]))
    def testMonitorTimeoutUnresponsive(self):
        with fake.VM(_VM_PARAMS) as testvm:
            self.assertEqual(testvm._monitorResponse, 0)
            self.assertFalse(testvm.isMigrating())
            stats = {'monitorResponse': '0'}
            testvm._setUnresponsiveIfTimeout(stats, 10)  # any value > timeout
            self.assertEqual(stats['monitorResponse'], '-1')

    @MonkeyPatch(vm, 'config',
                 make_config([('vars', 'vm_command_timeout', '10')]))
    def testMonitorTimeoutOnAlreadyUnresponsive(self):
        with fake.VM(_VM_PARAMS) as testvm:
            self._monitorResponse = -1
            self.assertFalse(testvm.isMigrating())
            stats = {'monitorResponse': '-1'}
            testvm._setUnresponsiveIfTimeout(stats, 1)  # any value < timeout
            self.assertEqual(stats['monitorResponse'], '-1')


class TestLibVirtCallbacks(TestCaseBase):
    FAKE_ERROR = 'EFAKERROR'

    def test_onIOErrorPause(self):
        with fake.VM(_VM_PARAMS, runCpu=True) as testvm:
            self.assertTrue(testvm._guestCpuRunning)
            testvm.onIOError('fakedev', self.FAKE_ERROR,
                             libvirt.VIR_DOMAIN_EVENT_IO_ERROR_PAUSE)
            self.assertFalse(testvm._guestCpuRunning)
            self.assertEqual(testvm.conf.get('pauseCode'), self.FAKE_ERROR)

    def test_onIOErrorReport(self):
        with fake.VM(_VM_PARAMS, runCpu=True) as testvm:
            self.assertTrue(testvm._guestCpuRunning)
            testvm.onIOError('fakedev', self.FAKE_ERROR,
                             libvirt.VIR_DOMAIN_EVENT_IO_ERROR_REPORT)
            self.assertTrue(testvm._guestCpuRunning)
            self.assertNotEquals(testvm.conf.get('pauseCode'), self.FAKE_ERROR)

    def test_onIOErrorNotSupported(self):
        """action not explicitely handled, must be skipped"""
        with fake.VM(_VM_PARAMS, runCpu=True) as testvm:
            self.assertTrue(testvm._guestCpuRunning)
            testvm.onIOError('fakedev', self.FAKE_ERROR,
                             libvirt.VIR_DOMAIN_EVENT_IO_ERROR_NONE)
            self.assertTrue(testvm._guestCpuRunning)
            self.assertNotIn('pauseCode', testvm.conf)  # no error recorded


@expandPermutations
class TestVmFunctions(TestCaseBase):

    def testGetPidNoFile(self):
        with MonkeyPatchScope([(vm, 'supervdsm',
                                fake.SuperVdsm(exception=IOError))]):
            with fake.VM() as testvm:
                self.assertRaises(IOError, testvm._getPid)

    def testGetPidBadFile(self):
        with MonkeyPatchScope([(vm, 'supervdsm',
                                fake.SuperVdsm(exception=ValueError))]):
            with fake.VM() as testvm:
                self.assertRaises(ValueError, testvm._getPid)

    @permutations([[-1], [0]])
    def testGetPidBadFileContent(self, pid):
        with MonkeyPatchScope([(vm, 'supervdsm',
                                fake.SuperVdsm(pid=pid))]):
            with fake.VM() as testvm:
                self.assertRaises(ValueError, testvm._getPid)

    def testGetPidSuccess(self):
        pid = 42
        with MonkeyPatchScope([(vm, 'supervdsm',
                                fake.SuperVdsm(pid=pid))]):
            with fake.VM() as testvm:
                self.assertEqual(testvm._getPid(), pid)


class TestVmStatusTransitions(TestCaseBase):
    @slowtest
    def testSavingState(self):
        with fake.VM(runCpu=True, status=vmstatus.UP) as testvm:
            testvm._dom = fake.Domain(domState=libvirt.VIR_DOMAIN_RUNNING)

            def _asyncEvent():
                testvm.onLibvirtLifecycleEvent(
                    libvirt.VIR_DOMAIN_EVENT_SUSPENDED,
                    -1, -1)

            t = threading.Thread(target=_asyncEvent)
            t.daemon = True

            def _fireAsyncEvent(*args):
                t.start()
                time.sleep(0.5)
                # pause the main thread to let the event one run

            with MonkeyPatchScope([(testvm, '_underlyingPause',
                                    _fireAsyncEvent)]):
                self.assertEqual(testvm.status()['status'], vmstatus.UP)
                testvm.pause(vmstatus.SAVING_STATE)
                self.assertEqual(testvm.status()['status'],
                                 vmstatus.SAVING_STATE)
                t.join()
                self.assertEqual(testvm.status()['status'],
                                 vmstatus.SAVING_STATE)
                # state must not change even after we are sure the event was
                # handled


class TestVmBalloon(TestCaseBase):
    def assertAPIFailed(self, res, specificErr=None):
        if specificErr is None:
            self.assertNotEqual(res['status']['code'], 0)
        else:
            self.assertEqual(res['status']['code'],
                             define.errCode[specificErr]['status']['code'])

    def testSucceed(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            target = 256
            res = testvm.setBalloonTarget(target)  # just to fit in 80 cols
            self.assertEqual(res['status']['code'], 0)
            self.assertEqual(testvm._dom.__calls__,
                             [('setMemory', (target,), {})])

    def testVmWithoutDom(self):
        with fake.VM() as testvm:
            self.assertAPIFailed(testvm.setBalloonTarget(128))

    def testTargetValueNotInteger(self):
        with fake.VM() as testvm:
            self.assertAPIFailed(testvm.setBalloonTarget('foobar'))

    def testLibvirtFailure(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(virtError=libvirt.VIR_ERR_INTERNAL_ERROR)
            # we don't care about the error code as long as is != NO_DOMAIN
            self.assertAPIFailed(testvm.setBalloonTarget(256), 'balloonErr')


@expandPermutations
class TestVmSanity(TestCaseBase):
    def testSmpPresentIfNotSpecified(self):
        with fake.VM() as testvm:
            self.assertEqual(int(testvm.conf['smp']), 1)

    @permutations([[1], [2], [4]])
    def testSmpByParameters(self, cpus):
        with fake.VM({'smp': cpus}) as testvm:
            self.assertEqual(int(testvm.conf['smp']), cpus)

    def testVmNameDefault(self):
        with fake.VM(_VM_PARAMS) as testvm:
            self.assertIn('vmName', testvm.getStats())

    def testVmNameExplicit(self):
        NAME = 'not the default VM name'
        params = {'vmName': NAME}
        params.update(_VM_PARAMS)
        with fake.VM(params) as testvm:
            self.assertEqual(NAME, testvm.getStats()['vmName'])


class ChangeBlockDevTests(TestCaseBase):

    def test_change_cd_eject(self):
        with fake.VM() as fakevm:
            fakevm._dom = fake.Domain()
            cdromspec = {'path': '',
                         'iface': 'ide',
                         'index': '2'}
            res = fakevm.changeCD(cdromspec)
            self.assertFalse(response.is_error(res))

    def test_change_cd_failure(self):
        cif = fake.ClientIF()
        with MonkeyPatchScope([(cif, 'prepareVolumePath',
                                lambda drive: drive),
                               (cif, 'teardownVolumePath',
                                lambda _: None)]):
            with fake.VM(cif=cif) as fakevm:
                # no specific meaning, actually any error != None is good
                fakevm._dom = fake.Domain(
                    virtError=libvirt.VIR_ERR_GET_FAILED)

                res = fakevm.changeCD('/path/to/image')

                expected_status = define.errCode['changeDisk']['status']
                self.assertEqual(res['status'], expected_status)


class TestingVm(vm.Vm):
    """
    Fake Vm required for testing code that does not care about vm state,
    invoking libvirt apis via Vm._dom, and logging via Vm.log.
    """

    log = logging.getLogger()

    def __init__(self, dom):
        self._dom = dom


class FreezingTests(TestCaseBase):

    def setUp(self):
        self.dom = fake.Domain()
        self.vm = TestingVm(self.dom)

    def test_freeze(self):
        res = self.vm.freeze()
        self.assertEqual(res, response.success())
        self.assertEqual(self.dom.__calls__, [("fsFreeze", (), {})])

    def test_thaw(self):
        res = self.vm.thaw()
        self.assertEqual(res, response.success())
        self.assertEqual(self.dom.__calls__, [("fsThaw", (), {})])


class FreezingGuestAgentUnresponsiveTests(TestCaseBase):

    expected = response.error("nonresp", message="fake error")

    def setUp(self):
        self.dom = fake.Domain(
            virtError=libvirt.VIR_ERR_AGENT_UNRESPONSIVE,
            errorMessage="fake error")
        self.vm = TestingVm(self.dom)

    def test_freeze(self):
        res = self.vm.freeze()
        self.assertEqual(res, self.expected)

    def test_thaw(self):
        res = self.vm.thaw()
        self.assertEqual(res, self.expected)


class FreezingUnsupportedTests(TestCaseBase):

    expected = response.error("unsupportedOperationErr", message="fake error")

    def setUp(self):
        self.dom = fake.Domain(
            virtError=libvirt.VIR_ERR_NO_SUPPORT,
            errorMessage="fake error")
        self.vm = TestingVm(self.dom)

    def test_freeze(self):
        res = self.vm.freeze()
        self.assertEqual(res, self.expected)

    def test_thaw(self):
        res = self.vm.thaw()
        self.assertEqual(res, self.expected)


class FreezingUnexpectedErrorTests(TestCaseBase):

    def setUp(self):
        self.dom = fake.Domain(
            virtError=libvirt.VIR_ERR_INTERNAL_ERROR,
            errorMessage="fake error")
        self.vm = TestingVm(self.dom)

    def test_freeze(self):
        res = self.vm.freeze()
        self.assertEqual(res, response.error("freezeErr",
                                             message="fake error"))

    def test_thaw(self):
        res = self.vm.thaw()
        self.assertEqual(res, response.error("thawErr",
                                             message="fake error"))


class BlockIoTuneTests(TestCaseBase):

    def setUp(self):
        self.iotune_low = {
            'total_bytes_sec': 0,
            'read_bytes_sec': 1000,
            'write_bytes_sec': 1000,
            'total_iops_sec': 0,
            'write_iops_sec': 0,
            'read_iops_sec': 0
        }
        self.iotune_high = {
            'total_bytes_sec': 0,
            'read_bytes_sec': 2000,
            'write_bytes_sec': 2000,
            'total_iops_sec': 0,
            'write_iops_sec': 0,
            'read_iops_sec': 0
        }
        self.drive = FakeBlockIoTuneDrive('vda', path='/fake/path/vda')

        self.dom = FakeBlockIoTuneDomain()
        self.dom.iotunes = {self.drive.name: self.iotune_low.copy()}

    @MonkeyPatch(vm, 'isVdsmImage', lambda *args: True)
    @MonkeyPatch(utils, 'isBlockDevice', lambda *args: False)
    def test_get_fills_cache(self):
        with fake.VM() as testvm:
            testvm._dom = self.dom
            testvm._devices[hwclass.DISK] = (self.drive,)

            res = testvm.getIoTuneResponse()
            self.assertFalse(response.is_error(res))
            self.assertEqual(
                self.dom.__calls__,
                [('blockIoTune',
                    (self.drive.name, libvirt.VIR_DOMAIN_AFFECT_LIVE),
                    {})]
            )

            res = testvm.getIoTuneResponse()
            self.assertFalse(response.is_error(res))
            self.assertEqual(
                self.dom.__calls__,
                [('blockIoTune',
                    (self.drive.name, libvirt.VIR_DOMAIN_AFFECT_LIVE),
                    {})]
            )

    @MonkeyPatch(vm, 'isVdsmImage', lambda *args: True)
    @MonkeyPatch(utils, 'isBlockDevice', lambda *args: False)
    def test_set_updates_cache(self):
        with fake.VM() as testvm:
            testvm._dom = self.dom
            testvm._devices[hwclass.DISK] = (self.drive,)

            tunables = [
                {"name": self.drive.name, "ioTune": self.iotune_high}
            ]

            res = testvm.getIoTuneResponse()
            self.assert_iotune_in_response(res, self.iotune_low)

            testvm.setIoTune(tunables)

            res = testvm.getIoTuneResponse()
            self.assert_iotune_in_response(res, self.iotune_high)

            self.assertEqual(len(self.dom.__calls__), 2)
            self.assert_nth_call_to_dom_is(0, 'blockIoTune')
            self.assert_nth_call_to_dom_is(1, 'setBlockIoTune')

    @MonkeyPatch(vm, 'isVdsmImage', lambda *args: True)
    @MonkeyPatch(utils, 'isBlockDevice', lambda *args: False)
    def test_set_fills_cache(self):
        with fake.VM() as testvm:
            testvm._dom = self.dom
            testvm._devices[hwclass.DISK] = (self.drive,)

            tunables = [
                {"name": self.drive.name, "ioTune": self.iotune_high}
            ]

            testvm.setIoTune(tunables)

            res = testvm.getIoTuneResponse()
            self.assert_iotune_in_response(res, self.iotune_high)

            self.assertEqual(len(self.dom.__calls__), 1)
            self.assert_nth_call_to_dom_is(0, 'setBlockIoTune')

    @MonkeyPatch(vm, 'isVdsmImage', lambda *args: True)
    @MonkeyPatch(utils, 'isBlockDevice', lambda *args: False)
    def test_cold_cache_set_preempts_get(self):
        with fake.VM() as testvm:
            testvm._dom = self.dom
            testvm._devices[hwclass.DISK] = (self.drive,)

            def _interleaved_update():
                # this will run in the middle of getIoTuneResponse()
                tunables = [
                    {"name": self.drive.name, "ioTune": self.iotune_high}
                ]
                testvm.setIoTune(tunables)

            self.dom.callback = _interleaved_update
            self.assert_iotune_in_response(
                testvm.getIoTuneResponse(),
                self.iotune_low
            )

            self.assertEqual(
                self.dom.iotunes,
                {self.drive.name: self.iotune_high}
            )

    def assert_nth_call_to_dom_is(self, nth, call):
        self.assertEqual(self.dom.__calls__[nth][0], call)

    def assert_iotune_in_response(self, res, iotune):
        self.assertEqual(
            res['ioTuneList'][0]['ioTune'], iotune
        )


class FakeBlockIoTuneDrive(object):

    def __init__(self, name, path=None):
        self.name = name
        self.path = path or os.path.join('fake', 'path', name)
        self.iotune = {}
        self.specParams = {}
        self._deviceXML = ''

    def _validateIoTuneParams(self, ioTuneParams):
        pass

    def getXML(self):
        return vmxml.Element('fake')


class FakeBlockIoTuneDomain(object):

    def __init__(self):
        self.iotunes = {}
        self.callback = None

    @recorded
    def blockIoTune(self, name, flags=0):
        ret = self.iotunes.get(name, {}).copy()
        if self.callback is not None:
            self.callback()
        return ret

    @recorded
    def setBlockIoTune(self, name, iotune, flags=0):
        self.iotunes[name] = iotune.copy()


@expandPermutations
class SyncGuestTimeTests(TestCaseBase):

    def _make_vm(self, virt_error=None):
        dom = fake.Domain(virtError=virt_error)
        return TestingVm(dom)

    @MonkeyPatch(time, 'time', lambda: 1234567890.125)
    def test_success(self):
        vm = self._make_vm()
        vm._syncGuestTime()
        self.assertEqual(vm._dom.__calls__, [
            ('setTime', (), {'time': {'seconds': 1234567890,
                                      'nseconds': 125000000}})
        ])

    @permutations([[libvirt.VIR_ERR_AGENT_UNRESPONSIVE],
                   [libvirt.VIR_ERR_NO_SUPPORT],
                   [libvirt.VIR_ERR_INTERNAL_ERROR]])
    def test_swallow_expected_errors(self, virt_error):
        vm = self._make_vm(virt_error=virt_error)
        with self.assertNotRaises():
            vm._syncGuestTime()


def _load_xml(name):
    test_path = os.path.realpath(__file__)
    data_path = os.path.join(os.path.split(test_path)[0], 'devices', 'data')

    with open(os.path.join(data_path, name), 'r') as f:
        return f.read()
