#
# Copyright IBM Corp. 2012
# Copyright 2013-2018 Red Hat, Inc.
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
from __future__ import division

import xml.etree.cElementTree as etree

import six
from six.moves import zip

from vdsm import constants
from vdsm import hugepages

from vdsm.common import cpuarch

from vdsm.virt import libvirtxml

from monkeypatch import MonkeyPatch
from testlib import XMLTestCase
from testlib import find_xml_element
from testlib import permutations, expandPermutations

import vmfakelib as fake


@expandPermutations
class TestLibvirtxml(XMLTestCase):

    def __init__(self, *args, **kwargs):
        XMLTestCase.__init__(self, *args, **kwargs)
        self.channelListener = None
        self.conf = {'vmName': 'testVm',
                     'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
                     'smp': '8', 'maxVCpus': '160',
                     'memSize': '1024', 'memGuaranteedSize': '512'}

    @permutations([
        # vm_name, escaped_name
        ['fake-vm', 'fake-vm'],
        ['r&d', 'r&amp;d'],
    ])
    def test_placeholder_domain(self, vm_name, escaped_name):
        expected_xml = """<domain type="qemu">
            <name>%s</name>
            <uuid>00-000</uuid>
            <memory unit="KiB">262144</memory>
            <os>
                <type arch="x86_64" machine="pc">hvm</type>
            </os>
        </domain>""" % (escaped_name,)
        self.assertXMLEqual(
            libvirtxml.make_placeholder_domain_xml(
                FakeMinimalVm(
                    name=vm_name,
                    id='00-000',
                    mem_size_mb=256 * 1024)),
            expected_xml
        )

    @permutations([
        # user_conf, domain_type
        [{}, 'kvm'],
        [{'kvmEnable': 'true'}, 'kvm'],
        [{'kvmEnable': 'false'}, 'qemu'],
    ])
    def test_minimal_domain_xml(self, user_conf, domain_type):
        expected_xml = """
          <domain type="{domain_type}"
                  xmlns:ns0="http://ovirt.org/vm/tune/1.0"
                  xmlns:ns1="http://ovirt.org/vm/1.0">
              <name>testVm</name>
              <uuid>9ffe28b6-6134-4b1e-8804-1185f49c436f</uuid>
              <memory>1048576</memory>
              <currentMemory>1048576</currentMemory>
              <vcpu current="8">160</vcpu>
              <devices/>
              <metadata>
                <ns0:qos/>
                <ns1:vm/>
              </metadata>
              <clock adjustment="0" offset="variable">
                <timer name="rtc" tickpolicy="catchup" />
                <timer name="pit" tickpolicy="delay" />
                <timer name="hpet" present="no" />
              </clock>
              <features>
                <acpi />
              </features>
           </domain>""".format(domain_type=domain_type)

        conf = {}
        conf.update(self.conf)
        conf.update(user_conf)
        domxml = libvirtxml.make_minimal_domain(
            libvirtxml.Domain(conf, self.log, cpuarch.X86_64)
        )
        self.assertXMLEqual(domxml.toxml(), expected_xml)

    def test_minimal_domain_xml_iothreads(self):
        expected_xml = """
          <domain type="kvm"
                  xmlns:ns0="http://ovirt.org/vm/tune/1.0"
                  xmlns:ns1="http://ovirt.org/vm/1.0">
              <name>testVm</name>
              <uuid>9ffe28b6-6134-4b1e-8804-1185f49c436f</uuid>
              <iothreads>2</iothreads>
              <memory>1048576</memory>
              <currentMemory>1048576</currentMemory>
              <vcpu current="8">160</vcpu>
              <devices/>
              <metadata>
                <ns0:qos/>
                <ns1:vm/>
              </metadata>
              <clock adjustment="0" offset="variable">
                <timer name="rtc" tickpolicy="catchup" />
                <timer name="pit" tickpolicy="delay" />
                <timer name="hpet" present="no" />
              </clock>
              <features>
                <acpi />
              </features>
           </domain>"""
        conf = {'numOfIoThreads': 2}
        conf.update(self.conf)
        domxml = libvirtxml.make_minimal_domain(
            libvirtxml.Domain(conf, self.log, cpuarch.X86_64)
        )
        self.assertXMLEqual(domxml.toxml(), expected_xml)

    def test_minimal_domain_xml_memory_limits(self):
        expected_xml = """
          <domain type="kvm"
                  xmlns:ns0="http://ovirt.org/vm/tune/1.0"
                  xmlns:ns1="http://ovirt.org/vm/1.0">
              <name>testVm</name>
              <uuid>9ffe28b6-6134-4b1e-8804-1185f49c436f</uuid>
              <memory>1048576</memory>
              <currentMemory>1048576</currentMemory>
              <maxMemory slots="2">2097152</maxMemory>
              <vcpu current="8">160</vcpu>
              <devices/>
              <metadata>
                <ns0:qos/>
                <ns1:vm/>
              </metadata>
              <clock adjustment="0" offset="variable">
                <timer name="rtc" tickpolicy="catchup" />
                <timer name="pit" tickpolicy="delay" />
                <timer name="hpet" present="no" />
              </clock>
              <features>
                <acpi />
              </features>
           </domain>"""
        conf = {'maxMemSize': 2048, 'maxMemSlots': 2}
        conf.update(self.conf)
        domxml = libvirtxml.make_minimal_domain(
            libvirtxml.Domain(conf, self.log, cpuarch.X86_64)
        )
        self.assertXMLEqual(domxml.toxml(), expected_xml)

    def test_parse_minimal_domain_xml(self):
        dom_xml = """
          <domain type="kvm"
                  xmlns:ns0="http://ovirt.org/vm/tune/1.0"
                  xmlns:ns1="http://ovirt.org/vm/1.0">
              <name>testVm</name>
              <uuid>9ffe28b6-6134-4b1e-8804-1185f49c436f</uuid>
              <iothreads>2</iothreads>
              <memory>1048576</memory>
              <currentMemory>1048576</currentMemory>
              <maxMemory slots="2">2097152</maxMemory>
              <vcpu current="8">160</vcpu>
              <devices/>
              <metadata>
                <ns0:qos/>
                <ns1:vm/>
              </metadata>
              <clock adjustment="0" offset="variable">
                <timer name="rtc" tickpolicy="catchup" />
                <timer name="pit" tickpolicy="delay" />
                <timer name="hpet" present="no" />
              </clock>
              <features>
                <acpi />
              </features>
           </domain>"""
        expected_conf = {
            'bootMenuEnable': 'false',
            'kvmEnable': 'true',
            'maxMemSize': 2048,
            'maxMemSlots': 2,
            'numOfIoThreads': '2',
            'smp': '8',
            'timeOffset': '0'
        }
        self.assertEqual(
            libvirtxml.parse_domain(dom_xml, cpuarch.X86_64),
            expected_conf
        )

    def test_parse_domain_fake_cpu_xml(self):
        dom_xml = """
          <domain type="kvm"
                  xmlns:ns0="http://ovirt.org/vm/tune/1.0"
                  xmlns:ns1="http://ovirt.org/vm/1.0">
              <name>testVm</name>
              <uuid>9ffe28b6-6134-4b1e-8804-1185f49c436f</uuid>
              <iothreads>2</iothreads>
              <memory>1048576</memory>
              <currentMemory>1048576</currentMemory>
              <maxMemory slots="2">2097152</maxMemory>
              <vcpu current="8">160</vcpu>
              <devices/>
              <metadata>
                <ns0:qos/>
                <ns1:vm/>
              </metadata>
              <clock adjustment="0" offset="variable">
                <timer name="rtc" tickpolicy="catchup" />
                <timer name="pit" tickpolicy="delay" />
                <timer name="hpet" present="no" />
              </clock>
              <features>
                <acpi />
              </features>
              <cpu match="exact">
                <topology cores="1" sockets="16" threads="1"/>
                  <numa>
                     <cell cpus="0" id="0" memory="1048576"/>
                  </numa>
              </cpu>
           </domain>"""
        expected_conf = {
            'bootMenuEnable': 'false',
            'kvmEnable': 'true',
            'maxMemSize': 2048,
            'maxMemSlots': 2,
            'numOfIoThreads': '2',
            'smp': '8',
            'timeOffset': '0',
            'cpuType': 'Unknown or Fake',
            'maxVCpus': '16',
            'smpCoresPerSocket': '1',
            'smpThreadsPerCore': '1',
            'guestNumaNodes': [{'cpus': '0', 'memory': '1024', 'nodeIndex': 0}]
        }
        self.assertEqual(
            libvirtxml.parse_domain(dom_xml, cpuarch.X86_64),
            expected_conf
        )

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
            domxml = libvirtxml.Domain(conf, self.log, cpuarch.X86_64)
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
            domxml = libvirtxml.Domain(vmConf, self.log, cpuarch.X86_64)
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
            domxml = libvirtxml.Domain(vmConf, self.log, cpuarch.PPC64)
            domxml.appendOs()
            xml = find_xml_element(domxml.toxml(), './os')
            self.assertXMLEqual(xml, osXML)

    def testFeaturesXML(self):
        featuresXML = """
            <features>
                  <acpi/>
            </features>"""
        domxml = libvirtxml.Domain(self.conf, self.log, cpuarch.X86_64)
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
        domxml = libvirtxml.Domain(conf, self.log, cpuarch.X86_64)
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
        domxml = libvirtxml.Domain(self.conf, self.log, cpuarch.X86_64)
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

    def testClockXML(self):
        clockXML = """
            <clock adjustment="-3600" offset="variable">
                <timer name="rtc" tickpolicy="catchup"/>
                <timer name="pit" tickpolicy="delay"/>
                <timer name="hpet" present="no"/>
            </clock>"""
        self.conf['timeOffset'] = '-3600'
        domxml = libvirtxml.Domain(self.conf, self.log, cpuarch.X86_64)
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
        domxml = libvirtxml.Domain(conf, self.log, cpuarch.X86_64)
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
              <vcpupin cpuset="0-1" vcpu="0"/>
              <vcpupin cpuset="2-3" vcpu="1"/>
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
        domxml = libvirtxml.Domain(vmConf, self.log, cpuarch.X86_64)
        domxml.appendCpu()
        domxml.appendNumaTune()
        xml = domxml.toxml()
        self.assertXMLEqual(find_xml_element(xml, "./cpu"), cpuXML)
        self.assertXMLEqual(find_xml_element(xml, "./cputune"), cputuneXML)
        self.assertXMLEqual(find_xml_element(xml, './numatune'), numatuneXML)

    def testSharedGuestNumaNodes(self):
        numaXML = """
              <numa>
                  <cell cpus="0-1" memory="5242880" memAccess="shared"/>
              </numa>
              """

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
                                     ]}

        vmConf.update(self.conf)
        domxml = libvirtxml.Domain(vmConf, self.log, cpuarch.X86_64)
        domxml.appendCpu(hugepages_shared=True)
        xml = domxml.toxml()
        self.assertXMLEqual(find_xml_element(xml, "./cpu/numa"), numaXML)

    def testChannelXML(self):
        channelXML = """
          <channel type="unix">
             <target name="%s" type="virtio"/>
             <source mode="bind" path="%s"/>
          </channel>"""
        path = '/tmp/channel-socket'
        name = 'org.linux-kvm.port.0'
        channelXML = channelXML % (name, path)
        domxml = libvirtxml.Domain(self.conf, self.log, cpuarch.X86_64)
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
            domxml = libvirtxml.Domain(vmConf, self.log, cpuarch.X86_64)
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
            domxml = libvirtxml.Domain(vmConf, self.log, cpuarch.PPC64)
            domxml.appendInput()
            xml = find_xml_element(domxml.toxml(), './devices/input')
            self.assertXMLEqual(xml, inputXML)

    def testMemoryBackingXMLDefault(self):
        memorybacking_xml = """
          <memoryBacking>
            <hugepages>
              <page size="2048" />
            </hugepages>
          </memoryBacking>"""

        domxml = libvirtxml.Domain(self.conf, self.log, cpuarch.X86_64)
        domxml.appendMemoryBacking(
            hugepages.DEFAULT_HUGEPAGESIZE[cpuarch.real()]
        )
        xml = find_xml_element(domxml.toxml(), './memoryBacking')
        self.assertXMLEqual(xml, memorybacking_xml)

    @MonkeyPatch(cpuarch, 'real', lambda: cpuarch.PPC64LE)
    def testMemoryBackingXMLDefaultPPC(self):
        memorybacking_xml = """
          <memoryBacking>
            <hugepages>
              <page size="16384" />
            </hugepages>
          </memoryBacking>"""

        domxml = libvirtxml.Domain(self.conf, self.log, cpuarch.PPC64LE)
        domxml.appendMemoryBacking(
            hugepages.DEFAULT_HUGEPAGESIZE[cpuarch.real()]
        )
        xml = find_xml_element(domxml.toxml(), './memoryBacking')
        self.assertXMLEqual(xml, memorybacking_xml)

    def testMemoryBackingXML(self):
        memorybacking_xml = """
          <memoryBacking>
            <hugepages>
              <page size="1048576" />
            </hugepages>
          </memoryBacking>"""

        domxml = libvirtxml.Domain(self.conf, self.log, cpuarch.X86_64)
        domxml.appendMemoryBacking(1048576)
        xml = find_xml_element(domxml.toxml(), './memoryBacking')
        self.assertXMLEqual(xml, memorybacking_xml)


class FakeMinimalVm(object):
    def __init__(self, id='00-0000', name='fake-vm', mem_size_mb=256):
        self.arch = cpuarch.X86_64
        self.id = id
        self.name = name
        self._mem_size_mb = mem_size_mb

    def mem_size_mb(self):
        return self._mem_size_mb
