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

from itertools import product
import re
import threading
import time
import uuid

import libvirt
from nose.plugins.skip import SkipTest

from virt import vm
from virt import vmdevices
from virt import vmexitreason
from virt.vmdevices import hwclass
from virt.vmtune import io_tune_merge, io_tune_dom_to_values, io_tune_to_dom
from virt import vmxml
from virt import vmstats
from virt import vmstatus
from vdsm import constants
from vdsm import define
from vdsm import password
from vdsm import response
from testlib import VdsmTestCase as TestCaseBase
from testlib import permutations, expandPermutations
from testlib import find_xml_element
from testlib import make_config
from testlib import XMLTestCase
import caps
import hooks
from vdsm import utils
from vdsm import libvirtconnection
from monkeypatch import MonkeyPatch, MonkeyPatchScope
from testlib import namedTemporaryDir
from vmTestsData import CONF_TO_DOMXML_X86_64
from vmTestsData import CONF_TO_DOMXML_PPC64
from vmTestsData import CONF_TO_DOMXML_NO_VDSM
import vmfakelib as fake

from testValidation import brokentest, slowtest


_VM_PARAMS = {
    'displayPort': -1,
    'displaySecurePort': -1,
    'display': 'qxl',
    'displayIp': '127.0.0.1',
    'vmType': 'kvm',
    'memSize': 1024
}


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

                    self.assertEqual(
                        re.sub('\n\s*', ' ', output.strip(' ')),
                        re.sub('\n\s*', ' ', expectedXML.strip(' ')))

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

        domxml = vmxml.Domain(self.conf, self.log, caps.Architecture.X86_64)
        self.assertXMLEqual(domxml.dom.toxml(), expectedXML)

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
        for conf, osXML in zip(vmConfs, expectedXMLs):
            conf.update(self.conf)
            domxml = vmxml.Domain(conf, self.log, caps.Architecture.X86_64)
            domxml.appendOs()
            xml = find_xml_element(domxml.dom.toxml(), './os')
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
        for k, v in qemu2libvirtBoot.iteritems():
            vmConfs.append({'boot': k})
            expectedXMLs.append(OSXML % v)

        for vmConf, osXML in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = vmxml.Domain(vmConf, self.log, caps.Architecture.X86_64)
            domxml.appendOs()
            xml = find_xml_element(domxml.dom.toxml(), './os')
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
        for k, v in qemu2libvirtBoot.iteritems():
            vmConfs.append({'boot': k})
            expectedXMLs.append(OSXML % v)

        for vmConf, osXML in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = vmxml.Domain(vmConf, self.log, caps.Architecture.PPC64)
            domxml.appendOs()
            xml = find_xml_element(domxml.dom.toxml(), './os')
            self.assertXMLEqual(xml, osXML)

    def testFeaturesXML(self):
        featuresXML = """
            <features>
                  <acpi/>
            </features>"""
        domxml = vmxml.Domain(self.conf, self.log, caps.Architecture.X86_64)
        domxml.appendFeatures()
        xml = find_xml_element(domxml.dom.toxml(), './features')
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
        domxml = vmxml.Domain(conf, self.log, caps.Architecture.X86_64)
        domxml.appendFeatures()
        xml = find_xml_element(domxml.dom.toxml(), './features')
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
        domxml = vmxml.Domain(self.conf, self.log, caps.Architecture.X86_64)
        domxml.appendSysinfo(product, version, serial)
        xml = find_xml_element(domxml.dom.toxml(), './sysinfo')
        self.assertXMLEqual(xml, sysinfoXML)

    def testClockXML(self):
        clockXML = """
            <clock adjustment="-3600" offset="variable">
                <timer name="rtc" tickpolicy="catchup"/>
                <timer name="pit" tickpolicy="delay"/>
                <timer name="hpet" present="no"/>
            </clock>"""
        self.conf['timeOffset'] = '-3600'
        domxml = vmxml.Domain(self.conf, self.log, caps.Architecture.X86_64)
        domxml.appendClock()
        xml = find_xml_element(domxml.dom.toxml(), './clock')
        self.assertXMLEqual(xml, clockXML)

    def testHyperVClockXML(self):
        clockXML = """
            <clock adjustment="-3600" offset="variable">
                <timer name="hypervclock"/>
                <timer name="pit" tickpolicy="delay"/>
                <timer name="hpet" present="no"/>
            </clock>"""
        conf = {'timeOffset': '-3600', 'hypervEnable': 'true'}
        conf.update(self.conf)
        domxml = vmxml.Domain(conf, self.log, caps.Architecture.X86_64)
        domxml.appendClock()
        xml = find_xml_element(domxml.dom.toxml(), './clock')
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
        domxml = vmxml.Domain(vmConf, self.log, caps.Architecture.X86_64)
        domxml.appendCpu()
        domxml.appendNumaTune()
        xml = domxml.dom.toxml()
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
        domxml = vmxml.Domain(self.conf, self.log, caps.Architecture.X86_64)
        domxml._appendAgentDevice(path, name)
        xml = find_xml_element(domxml.dom.toxml(), './devices/channel')
        self.assertXMLEqual(xml, channelXML)

    def testInputXMLX86_64(self):
        expectedXMLs = [
            """<input bus="ps2" type="mouse"/>""",
            """<input bus="usb" type="tablet"/>"""]

        vmConfs = [{}, {'tabletEnable': 'true'}]
        for vmConf, inputXML in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = vmxml.Domain(vmConf, self.log, caps.Architecture.X86_64)
            domxml.appendInput()
            xml = find_xml_element(domxml.dom.toxml(), './devices/input')
            self.assertXMLEqual(xml, inputXML)

    def testInputXMLPPC64(self):
        expectedXMLs = [
            """<input bus="usb" type="mouse"/>""",
            """<input bus="usb" type="tablet"/>"""]

        vmConfs = [{}, {'tabletEnable': 'true'}]
        for vmConf, inputXML in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = vmxml.Domain(vmConf, self.log, caps.Architecture.PPC64)
            domxml.appendInput()
            xml = find_xml_element(domxml.dom.toxml(), './devices/input')
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
            drive = vmdevices.storage.Drive(vmConf, self.log, **devConf)
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
    @MonkeyPatch(libvirtconnection, 'get', fake.Connection)
    @MonkeyPatch(utils, 'getHostUUID',
                 lambda: "fc25cbbe-5520-4f83-b82e-1541914753d9")
    def testBuildCmdLineX86_64(self):
        self.assertBuildCmdLine(CONF_TO_DOMXML_X86_64)

    @MonkeyPatch(caps, 'getTargetArch', lambda: caps.Architecture.PPC64)
    @MonkeyPatch(caps, 'osversion', lambda: {
        'release': '1', 'version': '18', 'name': 'Fedora'})
    @MonkeyPatch(libvirtconnection, 'get', fake.Connection)
    @MonkeyPatch(utils, 'getHostUUID',
                 lambda: "fc25cbbe-5520-4f83-b82e-1541914753d9")
    def testBuildCmdLinePPC64(self):
        self.assertBuildCmdLine(CONF_TO_DOMXML_PPC64)

    def testGetVmPolicySucceded(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            self.assertXMLEqual(testvm._getVmPolicy().toxml(), '<qos/>')

    def testGetVmPolicyEmptyOnNoMetadata(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(
                virtError=libvirt.VIR_ERR_NO_DOMAIN_METADATA)
            self.assertXMLEqual(testvm._getVmPolicy().toxml(), '<qos/>')

    def testGetVmPolicyFailOnNoDomain(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(virtError=libvirt.VIR_ERR_NO_DOMAIN)
            self.assertEqual(testvm._getVmPolicy(), None)

    def _xml_sanitizer(self, text):
        return re.sub(">[\t\n ]+<", "><", text).strip()

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

    def testGetIoTune(self):
        with fake.VM() as machine:
            dom = fake.Domain()
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
                        <guaranteed>
                            <totalBytes>9999</totalBytes>
                        </guaranteed>
                    </device>
                </ioTune>
            </qos>
            """
            machine._dom = dom

            tunables = machine.getIoTunePolicy()
            expected = [
                {'name': u'test-device-by-name',
                 'maximum': {
                     u'totalBytes': 9999
                 }},
                {'name': u'other-device',
                 'guaranteed': {
                     u'totalBytes': 9999
                 }}
            ]
            self.assertEqual(tunables, expected)

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
                iface="ide")
        ]

        # Make the drive look like a VDSM volume
        required = ('domainID', 'imageID', 'poolID', 'volumeID')
        for p in required:
            setattr(drives[0], p, "1")
        drives[0]._blockDev = True

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
                <serial></serial>
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
            self.assertEqual(self._xml_sanitizer(drives[0]._deviceXML),
                             self._xml_sanitizer(expected_xml))

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
                volumeID=uuid.uuid4()
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
            )
        ]

        with fake.VM() as machine:
            for drive in drives:
                machine._devices[drive.type].append(drive)

            self.assertEqual(machine.sdIds, set([domainID]))


@expandPermutations
class TestVmOperations(TestCaseBase):
    # just numbers, no particular meaning
    UPDATE_OFFSETS = [-3200, 3502, -2700, 3601]
    BASE_OFFSET = 42

    GRAPHIC_DEVICES = [{'type': 'graphics', 'device': 'spice', 'port': '-1'},
                       {'type': 'graphics', 'device': 'vnc', 'port': '-1'}]

    @MonkeyPatch(libvirtconnection, 'get', lambda x: fake.Connection())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetNotPresentByDefault(self, exitCode):
        with fake.VM() as testvm:
            testvm.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            self.assertFalse('timeOffset' in testvm.getStats())

    @MonkeyPatch(libvirtconnection, 'get', lambda x: fake.Connection())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetRoundtrip(self, exitCode):
        with fake.VM({'timeOffset': self.BASE_OFFSET}) as testvm:
            testvm.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            self.assertEqual(testvm.getStats()['timeOffset'],
                             self.BASE_OFFSET)

    @MonkeyPatch(libvirtconnection, 'get', lambda x: fake.Connection())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetRoundtriupAcrossInstances(self, exitCode):
        # bz956741
        lastOffset = 0
        for offset in self.UPDATE_OFFSETS:
            with fake.VM({'timeOffset': lastOffset}) as testvm:
                testvm._rtcUpdate(offset)
                testvm.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
                vmOffset = testvm.getStats()['timeOffset']
                self.assertEqual(vmOffset, str(lastOffset + offset))
                # the field in getStats is str, not int
                lastOffset = int(vmOffset)

    @MonkeyPatch(libvirtconnection, 'get', lambda x: fake.Connection())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetUpdateIfAbsent(self, exitCode):
        # bz956741 (-like, simpler case)
        with fake.VM() as testvm:
            for offset in self.UPDATE_OFFSETS:
                testvm._rtcUpdate(offset)
            # beware of type change!
            testvm.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            self.assertEqual(testvm.getStats()['timeOffset'],
                             str(self.UPDATE_OFFSETS[-1]))

    @MonkeyPatch(libvirtconnection, 'get', lambda x: fake.Connection())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetUpdateIfPresent(self, exitCode):
        with fake.VM({'timeOffset': self.BASE_OFFSET}) as testvm:
            for offset in self.UPDATE_OFFSETS:
                testvm._rtcUpdate(offset)
            # beware of type change!
            testvm.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            self.assertEqual(testvm.getStats()['timeOffset'],
                             str(self.BASE_OFFSET + self.UPDATE_OFFSETS[-1]))

    def testUpdateSingleDeviceGraphics(self):
        devXmls = (
            '<graphics connected="disconnect" passwd="12345678"'
            ' port="5900" type="spice"/>',
            '<graphics passwd="12345678" port="5900" type="vnc"/>')
        for device, devXml in zip(self.GRAPHIC_DEVICES, devXmls):
            domXml = '''
                <devices>
                    <graphics type="%s" port="5900" />
                </devices>''' % device['device']
            self._verifyDeviceUpdate(device, device, domXml, devXml)

    def testUpdateMultipleDeviceGraphics(self):
        devXmls = (
            '<graphics connected="disconnect" passwd="12345678"'
            ' port="5900" type="spice"/>',
            '<graphics passwd="12345678" port="5901" type="vnc"/>')
        domXml = '''
            <devices>
                <graphics type="spice" port="5900" />
                <graphics type="vnc" port="5901" />
            </devices>'''
        for device, devXml in zip(self.GRAPHIC_DEVICES, devXmls):
            self._verifyDeviceUpdate(
                device, self.GRAPHIC_DEVICES, domXml, devXml)

    def _verifyDeviceUpdate(self, device, allDevices, domXml, devXml):
        TICKET_PARAMS = {
            'userName': 'admin',
            'userId': 'fdfc627c-d875-11e0-90f0-83df133b58cc'}

        def _check_ticket_params(domXML, conf, params):
            self.assertEqual(params, TICKET_PARAMS)

        with MonkeyPatchScope([(hooks, 'before_vm_set_ticket',
                                _check_ticket_params)]):
            with fake.VM(devices=allDevices) as testvm:
                testvm._dom = fake.Domain(domXml)
                testvm.updateDevice({
                    'deviceType': 'graphics',
                    'graphicsType': device['device'],
                    'password': password.ProtectedPassword('12345678'),
                    'ttl': 0,
                    'existingConnAction': 'disconnect',
                    'params': TICKET_PARAMS})
                self.assertEquals(testvm._dom.devXml, devXml)

    def testDomainNotRunningWithoutDomain(self):
        with fake.VM() as testvm:
            self.assertEqual(testvm._dom, None)
            self.assertFalse(testvm._isDomainRunning())

    def testDomainNotRunningByState(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(domState=libvirt.VIR_DOMAIN_SHUTDOWN)
            self.assertFalse(testvm._isDomainRunning())

    def testDomainIsRunning(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(domState=libvirt.VIR_DOMAIN_RUNNING)
            self.assertTrue(testvm._isDomainRunning())

    def testDomainIsReadyForCommands(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            self.assertTrue(testvm.isDomainReadyForCommands())

    def testDomainDisappearedNotReadyForCommands(self):
        def _fail(*args):
            raise_libvirt_error(libvirt.VIR_ERR_NO_DOMAIN,
                                "Disappeared domain")

        with fake.VM() as testvm:
            dom = fake.Domain()
            dom.controlInfo = _fail
            testvm._dom = dom
            self.assertFalse(testvm.isDomainReadyForCommands())

    def testDomainNoneNotReadyForCommands(self):
        with fake.VM() as testvm:
            testvm._dom = None
            self.assertFalse(testvm.isDomainReadyForCommands())

    def testReadyForCommandsRaisesLibvirtError(self):
        def _fail(*args):
            # anything != NO_DOMAIN is good
            raise_libvirt_error(libvirt.VIR_ERR_INTERNAL_ERROR,
                                "Fake internal error")

        with fake.VM() as testvm:
            dom = fake.Domain()
            dom.controlInfo = _fail
            testvm._dom = dom
            self.assertRaises(libvirt.libvirtError,
                              testvm.isDomainReadyForCommands)

    def testReadPauseCodeDomainRunning(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(domState=libvirt.VIR_DOMAIN_RUNNING)
            self.assertEqual(testvm._readPauseCode(), 'NOERR')

    def testReadPauseCodeDomainPausedCrash(self):
        # REQUIRED_FOR: el6
        if not hasattr(libvirt, 'VIR_DOMAIN_PAUSED_CRASHED'):
            raise SkipTest('libvirt.VIR_DOMAIN_PAUSED_CRASHED undefined')

        with fake.VM() as testvm:
            # if paused for different reason we must not extend the disk
            # so anything else is ok
            dom = fake.Domain(domState=libvirt.VIR_DOMAIN_PAUSED,
                              domReason=libvirt.VIR_DOMAIN_PAUSED_CRASHED)
            testvm._dom = dom
            self.assertNotEqual(testvm._readPauseCode(), 'ENOSPC')

    def testReadPauseCodeDomainPausedENOSPC(self):
        with fake.VM() as testvm:
            dom = fake.Domain(domState=libvirt.VIR_DOMAIN_PAUSED,
                              domReason=libvirt.VIR_DOMAIN_PAUSED_IOERROR)
            dom.setDiskErrors({'vda': libvirt.VIR_DOMAIN_DISK_ERROR_NO_SPACE,
                               'hdc': libvirt.VIR_DOMAIN_DISK_ERROR_NONE})
            testvm._dom = dom
            self.assertEqual(testvm._readPauseCode(), 'ENOSPC')

    def testReadPauseCodeDomainPausedEIO(self):
        with fake.VM() as testvm:
            dom = fake.Domain(domState=libvirt.VIR_DOMAIN_PAUSED,
                              domReason=libvirt.VIR_DOMAIN_PAUSED_IOERROR)
            dom.setDiskErrors({'vda': libvirt.VIR_DOMAIN_DISK_ERROR_NONE,
                               'hdc': libvirt.VIR_DOMAIN_DISK_ERROR_UNSPEC})
            testvm._dom = dom
            self.assertEqual(testvm._readPauseCode(), 'EOTHER')

    @permutations([[1000, 24], [900, 0], [1200, -128]])
    def testSetCpuTuneQuote(self, quota, offset):
        with fake.VM() as testvm:
            # we need a different behaviour with respect to
            # plain fake.Domain. Seems simpler to just add
            # a new special-purpose trivial fake here.
            testvm._dom = ChangingSchedulerDomain(offset)
            testvm.setCpuTuneQuota(quota)
            self.assertEqual(quota + offset,
                             testvm._vcpuTuneInfo['vcpu_quota'])

    @permutations([[100000, 128], [150000, 0], [9999, -99]])
    def testSetCpuTunePeriod(self, period, offset):
        with fake.VM() as testvm:
            # same as per testSetCpuTuneQuota
            testvm._dom = ChangingSchedulerDomain(offset)
            testvm.setCpuTunePeriod(period)
            self.assertEqual(period + offset,
                             testvm._vcpuTuneInfo['vcpu_period'])

    @brokentest("sometimes on CI tries to connect to libvirt")
    @permutations([[libvirt.VIR_ERR_OPERATION_DENIED, 'setNumberOfCpusErr',
                    'Failed to set the number of cpus'],
                   [libvirt.VIR_ERR_NO_DOMAIN, 'noVM', None]])
    def testSetNumberOfVcpusFailed(self, virt_error, vdsm_error,
                                   error_message):
        def _fail(*args):
            raise_libvirt_error(virt_error, error_message)

        with MonkeyPatchScope([(hooks, 'before_set_num_of_cpus',
                                lambda: None)]):
            with fake.VM() as testvm:
                dom = fake.Domain()
                dom.setVcpusFlags = _fail
                testvm._dom = dom

                res = testvm.setNumberOfCpus(4)  # random value

                self.assertEqual(res, response.error(vdsm_error))


class ChangingSchedulerDomain(object):

    def __init__(self, offset=10):
        self._offset = offset
        self._params = {}

    def setSchedulerParameters(self, params):
        for k, v in params.items():
            self._params[k] = int(v) + self._offset

    def schedulerParameters(self):
        return self._params


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
        res = vmstats.nic(
            name='vnettest', model='virtio', mac=MAC,
            start_sample={'rx.bytes': 2 ** 64 - 15 * GBPS,
                          'rx.pkts': 1,
                          'rx.errs': 2,
                          'rx.drop': 3,
                          'tx.bytes': 0,
                          'tx.pkts': 4,
                          'tx.errs': 5,
                          'tx.drop': 6},
            end_sample={'rx.bytes': 0,
                        'rx.pkts': 7,
                        'rx.errs': 8,
                        'rx.drop': 9,
                        'tx.bytes': 5 * GBPS,
                        'tx.pkts': 10,
                        'tx.errs': 11,
                        'tx.drop': 12},
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
            'rxRate': '100.0', 'txRate': '33.3',
            'rx': '0', 'tx': '625000000',
        })

    def testMultipleGraphicDeviceStats(self):
        devices = [{'type': 'graphics', 'device': 'spice', 'port': '-1'},
                   {'type': 'graphics', 'device': 'vnc', 'port': '-1'}]

        with fake.VM(_VM_PARAMS, devices) as testvm:
            testvm._updateDevices(testvm.devSpecMapFromConf())
            res = testvm.getStats()
            self.assertIn('displayPort', res)
            self.assertEqual(res['displayType'],
                             'qxl' if devices[0]['device'] == 'spice' else
                             'vnc')
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
        with fake.VM(runCpu=True) as testvm:
            self.assertTrue(testvm._guestCpuRunning)
            testvm._onIOError('fakedev', self.FAKE_ERROR,
                              libvirt.VIR_DOMAIN_EVENT_IO_ERROR_PAUSE)
            self.assertFalse(testvm._guestCpuRunning)
            self.assertEqual(testvm.conf.get('pauseCode'), self.FAKE_ERROR)

    def test_onIOErrorReport(self):
        with fake.VM(runCpu=True) as testvm:
            self.assertTrue(testvm._guestCpuRunning)
            testvm._onIOError('fakedev', self.FAKE_ERROR,
                              libvirt.VIR_DOMAIN_EVENT_IO_ERROR_REPORT)
            self.assertTrue(testvm._guestCpuRunning)
            self.assertNotEquals(testvm.conf.get('pauseCode'), self.FAKE_ERROR)

    def test_onIOErrorNotSupported(self):
        """action not explicitely handled, must be skipped"""
        with fake.VM(runCpu=True) as testvm:
            self.assertTrue(testvm._guestCpuRunning)
            testvm._onIOError('fakedev', self.FAKE_ERROR,
                              libvirt.VIR_DOMAIN_EVENT_IO_ERROR_NONE)
            self.assertTrue(testvm._guestCpuRunning)
            self.assertNotIn('pauseCode', testvm.conf)  # no error recorded


@expandPermutations
class TestVmFunctions(TestCaseBase):

    _CONFS = {
        caps.Architecture.X86_64: CONF_TO_DOMXML_X86_64,
        caps.Architecture.PPC64: CONF_TO_DOMXML_PPC64,
        'novdsm': CONF_TO_DOMXML_NO_VDSM}

    def _buildAllDomains(self, arch):
        for conf, _ in self._CONFS[arch]:
            with fake.VM(conf, arch=arch) as v:
                domXml = v._buildDomainXML()
                yield fake.Domain(domXml, vmId=v.id), domXml

    def _getAllDomains(self, arch):
        for conf, rawXml in self._CONFS[arch]:
            domXml = rawXml % conf
            yield fake.Domain(domXml, vmId=conf['vmId']), domXml

    def _getAllDomainIds(self, arch):
        return [conf['vmId'] for conf, _ in self._CONFS[arch]]

    @permutations([[caps.Architecture.X86_64], [caps.Architecture.PPC64]])
    def testGetVDSMDomains(self, arch):
        with MonkeyPatchScope([(vm, '_listDomains',
                                lambda: self._buildAllDomains(arch)),
                               (caps, 'getTargetArch', lambda: arch)]):
            self.assertEqual([v.UUIDString() for v in vm.getVDSMDomains()],
                             self._getAllDomainIds(arch))

    # VDSM (of course) builds correct config, so we need static examples
    # of incorrect/not-compliant data
    def testSkipNotVDSMDomains(self):
        with MonkeyPatchScope([(vm, '_listDomains',
                                lambda: self._getAllDomains('novdsm'))]):
            self.assertFalse(vm.getVDSMDomains())

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
                testvm._onLibvirtLifecycleEvent(
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
            self.assertEqual(testvm._dom.calls['setMemory'][0], target)

    def testVmWithoutDom(self):
        with fake.VM() as testvm:
            self.assertTrue(testvm._dom is None)
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

    def test_change_cd_failure(self):
        cif = fake.ClientIF()
        with MonkeyPatchScope([(cif, 'prepareVolumePath',
                                lambda _: None),
                               (cif, 'teardownVolumePath',
                                lambda _: None)]):
            with fake.VM(cif=cif) as fakevm:
                # no specific meaning, actually any error != None is good
                fakevm._dom = fake.Domain(
                    virtError=libvirt.VIR_ERR_GET_FAILED)

                res = fakevm.changeCD({})

                expected_status = define.errCode['changeDisk']['status']
                self.assertEqual(res['status'], expected_status)


def raise_libvirt_error(code, message):
    err = libvirt.libvirtError(defmsg=message)
    err.err = [code]
    raise err
