#
# Copyright IBM Corp. 2012
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
import re
import xml.etree.ElementTree as ET
import libvirtvm
from vdsm import constants
from testrunner import VdsmTestCase as TestCaseBase


class TestLibvirtvm(TestCaseBase):

    PCI_ADDR = \
        'bus="0x00" domain="0x0000" function="0x0" slot="0x03" type="pci"'
    PCI_ADDR_DICT = {'slot': '0x03', 'bus': '0x00', 'domain': '0x0000',
                     'function': '0x0', 'type': 'pci'}

    def __init__(self, *args, **kwargs):
        TestCaseBase.__init__(self, *args, **kwargs)
        self.conf = {'vmName': 'testVm',
                     'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
                     'smp': '8', 'memSize': '1024'}

    def assertXML(self, element, expectedXML, path=None):
        if path is None:
            converted = element.toprettyxml()
        else:
            elem = ET.fromstring(element.toprettyxml())
            converted = re.sub(' />', '/>',
                        ET.tostring(elem.find("./%s" % path)))
        self.assertEqual(re.sub('\n\s*', ' ', converted).strip(' '),
                         re.sub('\n\s*', ' ', expectedXML).strip(' '))

    def testDomXML(self):
        expectedXML = """
           <domain type="kvm">
              <name>testVm</name>
              <uuid>9ffe28b6-6134-4b1e-8804-1185f49c436f</uuid>
              <memory>1048576</memory>
              <currentMemory>1048576</currentMemory>
              <vcpu>8</vcpu>
              <devices/>
           </domain>"""

        domxml = libvirtvm._DomXML(self.conf, self.log)
        self.assertXML(domxml.dom, expectedXML)

    def testOSXML(self):
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
            domxml = libvirtvm._DomXML(vmConf, self.log)
            domxml.appendOs()
            self.assertXML(domxml.dom, xml, 'os')

    def testFeaturesXML(self):
        featuresXML = """
            <features>
                  <acpi/>
            </features>"""
        domxml = libvirtvm._DomXML(self.conf, self.log)
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
        domxml = libvirtvm._DomXML(self.conf, self.log)
        domxml.appendSysinfo(product, version, serial)
        self.assertXML(domxml.dom, sysinfoXML, 'sysinfo')

    def testConsoleXML(self):
        consoleXML = """
            <console type="pty">
                <target port="0" type="virtio"/>
            </console>"""
        domxml = libvirtvm._DomXML(self.conf, self.log)
        domxml.appendConsole()
        self.assertXML(domxml.dom, consoleXML, 'devices/console')

    def testClockXML(self):
        clockXML = """
            <clock adjustment="-3600" offset="variable">
                <timer name="rtc" tickpolicy="catchup"/>
            </clock>"""
        self.conf['timeOffset'] = '-3600'
        domxml = libvirtvm._DomXML(self.conf, self.log)
        domxml.appendClock()
        self.assertXML(domxml.dom, clockXML, 'clock')

    def testCpuXML(self):
        cpuXML = """
          <cpu match="exact">
              <model>Opteron_G4</model>
              <topology cores="2" sockets="2" threads="2"/>
              <feature name="sse4.1" policy="require"/>
              <feature name="sse4.2" policy="require"/>
              <feature name="svm" policy="disable"/>
          </cpu> """
        cputuneXML = """
          <cputune>
              <vcpupin cpuset="2-3" vcpu="1"/>
              <vcpupin cpuset="0-1" vcpu="0"/>
          </cputune> """

        vmConf = {'cpuType': "Opteron_G4,+sse4_1,+sse4_2,-svm",
                  'smpCoresPerSocket': 2, 'smpThreadsPerCore': 2,
                  'cpuPinning': {'0': '0-1', '1': '2-3'}}
        vmConf.update(self.conf)
        domxml = libvirtvm._DomXML(vmConf, self.log)
        domxml.appendCpu()
        self.assertXML(domxml.dom, cpuXML, 'cpu')
        self.assertXML(domxml.dom, cputuneXML, 'cputune')

    def testChannelXML(self):
        channelXML = """
          <channel type="unix">
             <target name="%s" type="virtio"/>
             <source mode="bind" path="%s"/>
          </channel>"""
        path = '/tmp/channel-socket'
        name = 'org.linux-kvm.port.0'
        channelXML = channelXML % (name, path)
        domxml = libvirtvm._DomXML(self.conf, self.log)
        domxml._appendAgentDevice(path, name)
        self.assertXML(domxml.dom, channelXML, 'devices/channel')

    def testInputXML(self):
        expectedXMLs = [
            """<input bus="ps2" type="mouse"/>""",
            """<input bus="usb" type="tablet"/>"""]

        vmConfs = [{}, {'tabletEnable': 'true'}]
        for vmConf, xml in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = libvirtvm._DomXML(vmConf, self.log)
            domxml.appendInput()
            self.assertXML(domxml.dom, xml, 'devices/input')

    def testGraphicsXML(self):
        expectedXMLs = [
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
            </graphics>"""]

        spiceChannelXML = """
            <channel type="spicevmc">
                <target name="com.redhat.spice.0" type="virtio"/>
            </channel>"""

        vmConfs = [
            {'display': 'vnc', 'displayPort': '-1', 'displayNetwork':
             'vmDisplay', 'keyboardLayout': 'en-us'},

            {'display': 'qxl', 'displayPort': '-1', 'displaySecurePort': '-1',
             'spiceSecureChannels':
             "smain,sinputs,scursor,splayback,srecord,sdisplay"}]

        for vmConf, xml in zip(vmConfs, expectedXMLs):
            vmConf.update(self.conf)
            domxml = libvirtvm._DomXML(vmConf, self.log)
            domxml.appendGraphics()
            self.assertXML(domxml.dom, xml, 'devices/graphics')
            if vmConf['display'] == 'qxl':
                self.assertXML(domxml.dom, spiceChannelXML, 'devices/channel')

    def testBalloonXML(self):
        balloonXML = '<memballoon model="virtio"/>'
        dev = {'device': 'memballoon', 'type': 'balloon',
               'specParams': {'model': 'virtio'}}
        balloon = libvirtvm.BalloonDevice(self.conf, self.log, **dev)
        self.assertXML(balloon.getXML(), balloonXML)

    def testSoundXML(self):
        soundXML = '<sound model="ac97"/>'
        dev = {'device': 'ac97'}
        sound = libvirtvm.SoundDevice(self.conf, self.log, **dev)
        self.assertXML(sound.getXML(), soundXML)

    def testVideoXML(self):
        videoXML = """
            <video>
                <model heads="1" type="vga" vram="8192"/>
            </video>"""

        dev = {'device': 'vga', 'specParams': {'vram': '8192'}}
        video = libvirtvm.VideoDevice(self.conf, self.log, **dev)
        self.assertXML(video.getXML(), videoXML)

    def testInterfaceXML(self):
        interfaceXML = """
            <interface type="bridge"> <address %s/>
                <mac address="52:54:00:59:F5:3F"/>
                <model type="virtio"/>
                <source bridge="ovirtmgmt"/>
                <filterref filter="no-mac-spoofing"/>
                <boot order="1"/>
                <driver name="vhost"/>
                <tune>
                    <sndbuf>0</sndbuf>
                </tune>
            </interface>""" % self.PCI_ADDR

        dev = {'nicModel': 'virtio', 'macAddr': '52:54:00:59:F5:3F',
               'network': 'ovirtmgmt', 'address': self.PCI_ADDR_DICT,
               'device': 'bridge', 'type': 'interface',
               'bootOrder': '1', 'filter': 'no-mac-spoofing'}

        self.conf['custom'] = {'vhost': 'ovirtmgmt:true', 'sndbuf': '0'}
        iface = libvirtvm.NetworkInterfaceDevice(self.conf, self.log, **dev)
        self.assertXML(iface.getXML(), interfaceXML)

    def testControllerXML(self):
        devConfs = [
               {'device': 'ide', 'index': '0', 'address': self.PCI_ADDR_DICT},
               {'device': 'virtio-serial', 'address': self.PCI_ADDR_DICT},
               {'device': 'usb', 'model': 'ich9-ehci1', 'index': '0',
                'master': {'startport': '0'}, 'address': self.PCI_ADDR_DICT}]
        expectedXMLs = [
            """
            <controller index="0" type="ide">
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
            dev = libvirtvm.ControllerDevice(self.conf, self.log, **devConf)
            self.assertXML(dev.getXML(), xml % self.PCI_ADDR)

    def testRedirXML(self):
        redirXML = """
            <redirdev type="spicevmc">
                <address %s/>
            </redirdev>""" % self.PCI_ADDR

        dev = {'device': 'spicevmc', 'address': self.PCI_ADDR_DICT}

        redir = libvirtvm.RedirDevice(self.conf, self.log, **dev)
        self.assertXML(redir.getXML(), redirXML)

    def testDriveXML(self):
        SERIAL = '54-a672-23e5b495a9ea'
        devConfs = [
              {'index': '2', 'propagateErrors': 'off', 'iface': 'ide',
               'name': 'hdc', 'format': 'raw', 'device': 'cdrom',
               'path': '/tmp/fedora.iso', 'type': 'disk', 'readonly': 'True',
               'shared': 'False', 'serial': SERIAL},

              {'index': '0', 'propagateErrors': 'on', 'iface': 'virtio',
               'name': 'vda', 'format': 'cow', 'device': 'disk',
               'path': '/tmp/disk1.img', 'type': 'disk', 'readonly': 'False',
               'shared': 'True', 'serial': SERIAL},

              {'index': '0', 'propagateErrors': 'off', 'iface': 'virtio',
               'name': 'vda', 'format': 'raw', 'device': 'disk',
               'path': '/dev/mapper/lun1', 'type': 'disk', 'readonly': 'False',
               'shared': 'False', 'serial': SERIAL}]

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
            </disk>""",

             """
            <disk device="disk" snapshot="no" type="block">
                <source dev="/dev/mapper/lun1"/>
                <target bus="virtio" dev="vda"/>
                <serial>%s</serial>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="raw"/>
            </disk>"""]

        blockDevs = [False, False, True]
        vmConfs = [{}, {'custom': {'viodiskcache': 'writethrough'}}, {}]

        for (devConf, xml, blockDev, vmConf) in \
                      zip(devConfs, expectedXMLs, blockDevs, vmConfs):
            drive = libvirtvm.Drive(vmConf, self.log, **devConf)
            # Patch Drive.blockDev to skip the block device checking.
            drive._blockDev = blockDev
            self.assertXML(drive.getXML(), xml % SERIAL)
