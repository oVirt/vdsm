#
# Copyright 2017 Red Hat, Inc.
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

import logging
import os.path

from vdsm.virt.domain_descriptor import DomainDescriptor
from vdsm.virt import metadata
from vdsm.virt import vmdevices
from vdsm.virt import vmxml
from vdsm import constants
from vdsm import hostdev
from vdsm import utils

from monkeypatch import MonkeyPatchScope, MonkeyPatch
from testlib import make_config
from testlib import permutations, expandPermutations
from testlib import XMLTestCase

import vmfakecon as fake
import hostdevlib


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

    def test_console_virtio(self):
        consoleXML = """
            <console type="pty">
                <target port="0" type="virtio"/>
            </console>"""
        dev = {
            'device': 'console',
            'specParams': {'consoleType': 'virtio'},
            'vmid': self.conf['vmId'],
        }
        console = vmdevices.core.Console(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(console.getXML()), consoleXML)

    def test_console_serial(self):
        consoleXML = """
            <console type="pty">
                <target port="0" type="serial"/>
            </console>"""
        dev = {
            'device': 'console',
            'specParams': {'consoleType': 'serial'},
            'vmid': self.conf['vmId'],
        }
        console = vmdevices.core.Console(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(console.getXML()), consoleXML)

    def test_console_default(self):
        consoleXML = """
            <console type="pty">
                <target port="0" type="virtio"/>
            </console>"""
        dev = {
            'device': 'console',
            'vmid': self.conf['vmId'],
        }
        console = vmdevices.core.Console(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(console.getXML()), consoleXML)

    def test_serial_device(self):
        serialXML = """
            <serial type="pty">
                <target port="0"/>
            </serial>"""
        dev = {
            'device': 'console',
            'vmid': self.conf['vmId'],
        }
        console = vmdevices.core.Console(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(console.getSerialDeviceXML()),
                            serialXML)

    def test_unix_socket_serial_device(self):
        path = "/var/run/ovirt-vmconsole-console/%s.sock" % self.conf['vmId']
        serialXML = """
            <serial type="unix">
                <source mode="bind" path="%s" />
                <target port="0" />
            </serial>""" % path
        dev = {
            'vmid': self.conf['vmId'],
            'device': 'console',
            'specParams': {
                'enableSocket': True
            }
        }
        console = vmdevices.core.Console(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(console.getSerialDeviceXML()),
                            serialXML)

    def test_smartcard(self):
        smartcardXML = '<smartcard mode="passthrough" type="spicevmc"/>'
        dev = {'device': 'smartcard',
               'specParams': {'mode': 'passthrough', 'type': 'spicevmc'}}
        smartcard = vmdevices.core.Smartcard(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(smartcard.getXML()),
                            smartcardXML)

    def test_tpm(self):
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
        self.assertXMLEqual(vmxml.format_xml(tpm.getXML()), tpmXML)

    @permutations([[None], [{}], [{'enableSocket': False}]])
    def test_console_pty(self, specParams):
        consoleXML = """
            <console type="pty">
                <target port="0" type="virtio"/>
            </console>"""
        dev = {'device': 'console'}
        if specParams is not None:
            dev['specParams'] = specParams
        console = vmdevices.core.Console(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(console.getXML()), consoleXML)

    def test_console_socket(self):
        consoleXML = """
            <console type="unix">
                <source mode="bind" path="%s%s.sock" />
                <target port="0" type="virtio"/>
            </console>""" % (constants.P_OVIRT_VMCONSOLES,
                             self.conf['vmId'])
        dev = {'device': 'console', 'specParams': {'enableSocket': True}}
        dev['vmid'] = self.conf['vmId']
        console = vmdevices.core.Console(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(console.getXML()), consoleXML)

    def test_balloon(self):
        balloonXML = '<memballoon model="virtio"/>'
        dev = {'device': 'memballoon', 'type': 'balloon',
               'specParams': {'model': 'virtio'}}
        balloon = vmdevices.core.Balloon(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(balloon.getXML()), balloonXML)

    def test_rng(self):
        rngXML = """
            <rng model="virtio">
                <rate bytes="1234" period="2000"/>
                <backend model="random">/dev/random</backend>
            </rng>"""

        dev = {'type': 'rng', 'model': 'virtio', 'specParams':
               {'period': '2000', 'bytes': '1234', 'source': 'random'}}

        rng = vmdevices.core.Rng(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(rng.getXML()), rngXML)

    def test_watchdog(self):
        watchdogXML = '<watchdog action="none" model="i6300esb"/>'
        dev = {'device': 'watchdog', 'type': 'watchdog',
               'specParams': {'model': 'i6300esb', 'action': 'none'}}
        watchdog = vmdevices.core.Watchdog(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(watchdog.getXML()), watchdogXML)

    def test_sound(self):
        soundXML = '<sound model="ac97"/>'
        dev = {'device': 'ac97'}
        sound = vmdevices.core.Sound(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(sound.getXML()), soundXML)

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
    def test_video(self, dev_spec, video_xml):
        video = vmdevices.core.Video(self.log, **dev_spec)
        self.assertXMLEqual(vmxml.format_xml(video.getXML()), video_xml)

    def test_controller(self):
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
            self.assertXMLEqual(vmxml.format_xml(device.getXML()),
                                xml % self.PCI_ADDR)

    def test_redir(self):
        redirXML = """
            <redirdev type="spicevmc">
                <address %s/>
            </redirdev>""" % self.PCI_ADDR

        dev = {'device': 'spicevmc', 'address': self.PCI_ADDR_DICT}

        redir = vmdevices.core.Redir(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(redir.getXML()), redirXML)

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
        memory = vmdevices.core.Memory(self.log, **params)
        self.assertXMLEqual(vmxml.format_xml(memory.getXML()), memoryXML)

    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_interface(self):
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
               'vm_custom': {'vhost': 'ovirtmgmt:true', 'sndbuf': '0'},
               }
        iface = vmdevices.network.Interface(self.log, **dev)
        self.assertXMLEqual(vmxml.format_xml(iface.getXML()), interfaceXML)

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
        self.assertXMLEqual(vmxml.format_xml(iface.getXML()), interfaceXML)

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
            self.assertXMLEqual(vmxml.format_xml(iface.getXML()), interfaceXML)

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
            self.assertXMLEqual(vmxml.format_xml(vnic_xml), XML)


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
        dev = vmxml.parse_xml(XML)
        found_addr = vmdevices.core.find_device_guest_address(dev)
        found_alias = vmdevices.core.find_device_alias(dev)
        self.assertEqual(found_addr, self.ADDR)
        self.assertEqual(found_alias, self.ALIAS)

    def test_missing_address(self):
        XML = u"""<device type='fake'>
          <alias name='{alias}'/>
        </device>""".format(alias=self.ALIAS)
        dev = vmxml.parse_xml(XML)
        found_addr = vmdevices.core.find_device_guest_address(dev)
        found_alias = vmdevices.core.find_device_alias(dev)
        self.assertIs(found_addr, None)
        self.assertEqual(found_alias, self.ALIAS)

    def test_missing_alias(self):
        params = self.ADDR.copy()
        XML = u"""<device type='fake'>
          <address domain='{domain}' bus='{bus}'
            slot='{slot}' function='{function}'/>
        </device>""".format(**params)
        dev = vmxml.parse_xml(XML)
        found_addr = vmdevices.core.find_device_guest_address(dev)
        found_alias = vmdevices.core.find_device_alias(dev)
        self.assertEqual(found_addr, self.ADDR)
        self.assertEqual(found_alias, '')

    def test_missing_address_alias(self):
        XML = u"<device type='fake' />"
        dev = vmxml.parse_xml(XML)
        found_addr = vmdevices.core.find_device_guest_address(dev)
        found_alias = vmdevices.core.find_device_alias(dev)
        self.assertIs(found_addr, None)
        self.assertEqual(found_alias, '')

    def test_attrs(self):
        XML = u"<device type='fake' />"
        attrs = vmdevices.core.parse_device_attrs(
            vmxml.parse_xml(XML), ('type',)
        )
        self.assertEqual(attrs, {'type': 'fake'})

    def test_attrs_missing(self):
        XML = u"<device type='fake' />"
        attrs = vmdevices.core.parse_device_attrs(
            vmxml.parse_xml(XML), ('type', 'foo')
        )
        self.assertEqual(attrs, {'type': 'fake'})

    def test_attrs_partial(self):
        XML = u"<device foo='bar' ans='42' fizz='buzz' />"
        attrs = vmdevices.core.parse_device_attrs(
            vmxml.parse_xml(XML), ('foo', 'fizz')
        )
        self.assertEqual(attrs, {'foo': 'bar', 'fizz': 'buzz'})

    @permutations([
        # xml_data, dev_type
        [u'''<interface type='network' />''', 'network'],
        [u'''<console type="pty" />''', 'pty'],
        [u'''<controller type='usb' index='0' />''', 'usb'],
        [u'''<sound model="ac97"/>''', 'sound'],
        [u'''<tpm model='tpm-tis'/>''', 'tpm'],
    ])
    def test_find_device_type(self, xml_data, dev_type):
        self.assertEqual(
            dev_type,
            vmdevices.core.find_device_type(vmxml.parse_xml(xml_data))
        )

    @permutations([
        # xml_data, alias
        # well formed XMLs
        [u'''<interface><alias name="net0" /></interface>''', 'net0'],
        [u'''<console type="pty" />''', ''],
        # malformed XMLs
        [u'''<controller><alias>foobar</alias></controller>''', ''],
    ])
    def test_find_device_alias(self, xml_data, alias):
        self.assertEqual(
            alias,
            vmdevices.core.find_device_alias(vmxml.parse_xml(xml_data))
        )

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
        self.assertEqual(
            address,
            vmdevices.core.find_device_guest_address(
                vmxml.parse_xml(xml_data)
            )
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

    def appropriateIommuGroup(self, group):
        pass

    def rmAppropriateIommuGroup(self, group):
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

_GRAPHICS_DATA = [
    # graphics_xml, display_ip, meta, src_ports, expected_ports
    # both port requested, some features disabled
    [
        u'''<graphics type='spice' port='{port}' tlsPort='{tls_port}'
                  autoport='yes' keymap='en-us'
                  defaultMode='secure' passwd='*****'
                  passwdValidTo='1970-01-01T00:00:01'>
          <clipboard copypaste='no'/>
          <filetransfer enable='no'/>
          <listen type='network' network='vdsm-ovirtmgmt'/>
        </graphics>''',
        '127.0.0.1',
        {},
        {'port': '5900', 'tls_port': '5901'},
        {'port': '-1', 'tls_port': '-1'},
    ],
    # only insecure port requested
    [
        u'''<graphics type='vnc' port='{port}' autoport='yes'
                   keymap='en-us'
                   defaultMode="secure" passwd="*****"
                   passwdValidTo='1970-01-01T00:00:01'>
           <listen type='network' network='vdsm-ovirtmgmt'/>
        </graphics>''',
        '192.168.1.1',
        {},
        {'port': '5900', 'tls_port': '5901'},
        {'port': '-1', 'tls_port': '-1'},
    ],
    # listening on network, preserving autoselect
    [
        u'''<graphics type='vnc' port='{port}' autoport='yes'
                   keymap='en-us'
                   defaultMode="secure" passwd="*****"
                   passwdValidTo='1970-01-01T00:00:01'>
           <listen type='network' network='vdsm-ovirtmgmt'/>
        </graphics>''',
        '192.168.1.1',
        {},
        {'port': '-1', 'tls_port': '-1'},
        {'port': '-1', 'tls_port': '-1'},
    ],

]

_STORAGE_TEST_DATA = [
    [u'''<disk device="disk" snapshot="no" type="block">
            <source dev="/path/to/volume"/>
            <target bus="virtio" dev="vda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="none" discard="unmap" error_policy="stop"
                    io="native" name="qemu" type="raw"/>
        </disk>''',
     True,
     {}],
    [u'''<disk device="disk" snapshot="no" type="block">
            <source dev="/path/to/volume"/>
            <target bus="virtio" dev="vda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="none" error_policy="stop"
                    io="native" name="qemu" type="raw"/>
        </disk>''',
     True,
     {}],
    [u'''<disk device="disk" snapshot="no" type="file">
            <source file="/path/to/volume"/>
            <target bus="virtio" dev="vda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="none" error_policy="stop"
                    io="threads" name="qemu" type="raw"/>
        </disk>''',
     False,
     {}],
    [u'''<disk device="lun" sgio="unfiltered" snapshot="no" type="block">
            <source dev="/dev/mapper/lun1"/>
            <target bus="scsi" dev="sda"/>
            <driver cache="none" error_policy="stop"
                    io="native" name="qemu" type="raw"/>
        </disk>''',
     True,
     {}],
    [u'''<disk device="disk" snapshot="no" type="network">
            <source name="poolname/volumename" protocol="rbd">
                <host name="1.2.3.41" port="6789" transport="tcp"/>
                <host name="1.2.3.42" port="6789" transport="tcp"/>
            </source>
            <target bus="virtio" dev="vda"/>
            <serial>54-a672-23e5b495a9ea</serial>
            <driver cache="none" error_policy="stop"
                    io="threads" name="qemu" type="raw"/>
        </disk>''',
     False,
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
     False,
     {}],
    [u'''<disk device="lun" sgio="unfiltered" snapshot="no" type="block">
            <address bus="0" controller="0" target="0" type="drive" unit="0" />
            <source dev="/dev/mapper/36001405b3b7829f14c1400d925eefebb" />
            <target bus="scsi" dev="sda" />
            <driver cache="none" error_policy="stop" io="native"
                    name="qemu" type="raw" />
        </disk>''',
     True,
     {}],
    [u'''<disk device="cdrom" snapshot="no" type="file">
            <source file="/var/run/vdsm/payload/{guid}.{hashsum}.img"
                startupPolicy="optional" />
            <target bus="ide" dev="hdd" />
            <readonly />
        </disk>'''.format(guid='8a1dc504-9d00-48f3-abdc-c70404e6f7e2',
                          hashsum='4137dc5fb55e021fbfd2653621d9d194'),
     True,
     {}],
    [u'''<disk device="disk" snapshot="no" type="block">
            <source dev="/path/to/volume"/>
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
     True,
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


@expandPermutations
class DeviceXMLRoundTripTests(XMLTestCase):

    def test_base_not_implemented(self):
        # simplified version of channel XML, only for test purposes.
        # this should never be seen in the wild
        generic_xml = '<channel type="spicevmc" />'
        try:
            vmdevices.core.Base.from_xml_tree(
                self.log,
                vmxml.parse_xml(generic_xml),
                meta={'vmid': 'VMID'}
            )
        except NotImplementedError as exc:
            self.assertEqual(
                vmdevices.core.Base.__name__,
                str(exc)
            )
        except Exception as ex:
            raise AssertionError('from_xml_tree raise unexpected %s', ex)
        else:
            raise AssertionError('from_xml_tree implemented')

    def test_generic(self):
        # simplified version of channel XML, only for test purposes.
        # this should never be seen in the wild
        generic_xml = '<channel type="spicevmc" />'
        self._check_roundtrip(vmdevices.core.Generic, generic_xml)

    @permutations([
        # sound_xml
        [u'''<sound model="ac97"/>'''],
        [u'''<sound model='es1370'/>'''],
    ])
    def test_sound(self, sound_xml):
        self._check_roundtrip(vmdevices.core.Sound, sound_xml)

    def test_balloon(self):
        balloon_xml = u'''<memballoon model='virtio'>
          <address type='pci' domain='0x0000' bus='0x00' slot='0x04'
           function='0x0'/>
        </memballoon>'''
        self._check_roundtrip(vmdevices.core.Balloon, balloon_xml)

    @permutations([
        # console_type, is_serial
        ['virtio', False],
        ['serial', True],
    ])
    def test_console_pty(self, console_type, is_serial):
        console_xml = u'''<console type="pty">
            <target port="0" type="%s" />
        </console>''' % console_type
        self._check_roundtrip(
            vmdevices.core.Console, console_xml, meta={'vmid': 'VMID'}
        )

    @permutations([
        # console_type, is_serial
        ['virtio', False],
        ['serial', True],
    ])
    def test_console_pty_properties(self, console_type, is_serial):
        console_xml = u'''<console type="pty">
            <target port="0" type="%s" />
        </console>''' % console_type
        dev = vmdevices.core.Console.from_xml_tree(
            self.log,
            vmxml.parse_xml(console_xml),
            meta={'vmid': 'VMID'}
        )
        self.assertEqual(dev.isSerial, is_serial)

    @permutations([
        # console_type, is_serial
        ['virtio', False],
        ['serial', True],
    ])
    def test_console_unix_socket(self, console_type, is_serial):
        vmid = 'VMID'
        console_xml = u'''<console type='unix'>
          <source mode='bind' path='{sockpath}.sock' />
          <target type='{console_type}' port='0' />
        </console>'''.format(
            sockpath=os.path.join(constants.P_OVIRT_VMCONSOLES, vmid),
            console_type=console_type
        )
        self._check_roundtrip(
            vmdevices.core.Console, console_xml, meta={'vmid': vmid}
        )

    @permutations([
        # console_type, is_serial
        ['virtio', False],
        ['serial', True],
    ])
    def test_console_unix_socket_properties(self, console_type, is_serial):
        vmid = 'VMID'
        console_xml = u'''<console type='unix'>
          <source mode='bind' path='{sockpath}.sock' />
          <target type='{console_type}' port='0' />
        </console>'''.format(
            sockpath=os.path.join(constants.P_OVIRT_VMCONSOLES, vmid),
            console_type=console_type
        )
        dev = vmdevices.core.Console.from_xml_tree(
            self.log,
            vmxml.parse_xml(console_xml),
            meta={'vmid': vmid}
        )
        self.assertEqual(dev.isSerial, is_serial)
        self.assertEqual(dev.vmid, vmid)
        self.assertTrue(dev.specParams['enableSocket'])

    @permutations(_CONTROLLERS_XML)
    def test_controller(self, controller_xml):
        self._check_roundtrip(vmdevices.core.Controller, controller_xml)

    def test_smartcard(self):
        smartcard_xml = u'''<smartcard mode='passthrough' type='spicevmc'>
            <address type='ccid' controller='0' slot='0'/>
        </smartcard>'''
        self._check_roundtrip(vmdevices.core.Smartcard, smartcard_xml)

    def test_redir(self):
        redir_xml = u'''<redirdev bus='usb' type='spicevmc'>
          <address type='usb' bus='0' port='1'/>
        </redirdev>'''
        self._check_roundtrip(vmdevices.core.Redir, redir_xml)

    def test_video(self):
        video_xml = u'''<video>
          <address type='pci' domain='0x0000'
           bus='0x00' slot='0x02' function='0x0'/>
          <model type='qxl' ram='65536' vram='32768' vgamem='16384' heads='1'/>
        </video>'''
        self._check_roundtrip(vmdevices.core.Video, video_xml)

    @permutations([
        # rate_present
        [True],
        [False]
    ])
    @MonkeyPatch(vmdevices.core.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_rng(self, rate_present):
        rate = '<rate period="2000" bytes="1234"/>' if rate_present else ''
        rng_xml = u'''<rng model='virtio'>
            %s
            <backend model='random'>/dev/random</backend>
        </rng>''' % (rate)
        self._check_roundtrip(
            vmdevices.core.Rng, rng_xml, meta={'vmid': 'VMID'}
        )

    def test_tpm(self):
        tpm_xml = u'''<tpm model='tpm-tis'>
            <backend type='passthrough'>
                <device path='/dev/tpm0' />
            </backend>
        </tpm>'''
        self._check_roundtrip(vmdevices.core.Tpm, tpm_xml)

    def test_watchdog(self):
        watchdog_xml = u'''<watchdog model='i6300esb' action='reset'>
          <address type='pci' domain='0x0000' bus='0x00' slot='0x05'
           function='0x0'/>
        </watchdog>'''
        self._check_roundtrip(vmdevices.core.Watchdog, watchdog_xml)

    def test_memory(self):
        memory_xml = u'''<memory model='dimm'>
            <target>
                <size unit='KiB'>524288</size>
                <node>1</node>
            </target>
            <alias name='dimm0'/>
            <address type='dimm' slot='0' base='0x100000000'/>
        </memory>'''
        self._check_roundtrip(vmdevices.core.Memory, memory_xml)

    def test_lease(self):
        lease_xml = u'''<lease>
            <key>12523e3d-ad22-410c-8977-d2a7bf458a65</key>
            <lockspace>c2a6d7c8-8d81-4e01-9ed4-7eb670713448</lockspace>
            <target offset="1048576"
                    path="/dev/c2a6d7c8-8d81-4e01-9ed4-7eb670713448/leases"/>
        </lease>'''
        self._check_roundtrip(vmdevices.lease.Device, lease_xml)

    @permutations(_GRAPHICS_DATA)
    def test_graphics(self, graphics_xml, display_ip, meta,
                      src_ports, expected_ports):
        meta['vmid'] = 'VMID'
        with MonkeyPatchScope([
            (vmdevices.graphics, '_getNetworkIp', lambda net: display_ip),
            (vmdevices.graphics.libvirtnetwork,
                'create_network', lambda net, vmid: None),
            (vmdevices.graphics.libvirtnetwork,
                'delete_network', lambda net, vmid: None),
            (vmdevices.graphics, 'config',
                make_config([('vars', 'ssl', 'true')])),
        ]):
            self._check_roundtrip(
                vmdevices.graphics.Graphics,
                graphics_xml.format(**src_ports),
                meta=meta,
                expected_xml=graphics_xml.format(**expected_ports)
            )

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
        self._check_roundtrip(vmdevices.network.Interface, interface_xml)

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
                <boot order="9"/>
                <driver name="vfio"/>
            </interface>'''
        with MonkeyPatchScope([
            (hostdev, 'libvirtconnection', FakeLibvirtConnection())
        ]):
            self._check_roundtrip(vmdevices.network.Interface, interface_xml)

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
                <boot order="9"/>
                <driver name="vfio"/>
            </interface>'''
        with MonkeyPatchScope([
            (hostdev, 'libvirtconnection', FakeLibvirtConnection())
        ]):
            self._check_roundtrip(vmdevices.network.Interface, interface_xml)

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
              <driver name='vfio'/>
            </interface>'''
        with MonkeyPatchScope([
            (hostdev.libvirtconnection, 'get', hostdevlib.Connection),
            (vmdevices.hostdevice, 'detach_detachable',
                lambda *args, **kwargs: None),
            (vmdevices.hostdevice, 'reattach_detachable',
                lambda *args, **kwargs: None),
        ]):
            self._check_roundtrip(vmdevices.network.Interface, interface_xml)

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
                <boot order="1"/>
                <driver name="vhost" queues="4"/>
                <tune>
                    <sndbuf>128</sndbuf>
                </tune>
            </interface>'''

        with MonkeyPatchScope([
            (vmdevices.network.supervdsm, 'getProxy', lambda: proxy)
        ]):
            self._check_roundtrip(
                vmdevices.network.Interface,
                interface_xml,
                expected_xml=expected_xml
            )

    # TODO: add test with OVS and DPDK enabled

    @permutations(_HOSTDEV_XML)
    @MonkeyPatch(vmdevices.network.supervdsm,
                 'getProxy', lambda: FakeProxy())
    def test_hostdev(self, hostdev_xml):
        with MonkeyPatchScope([
            (hostdev.libvirtconnection, 'get', hostdevlib.Connection),
            (vmdevices.hostdevice, 'detach_detachable',
                lambda *args, **kwargs: None),
            (vmdevices.hostdevice, 'reattach_detachable',
                lambda *args, **kwargs: None),
        ]):
            self._check_roundtrip(vmdevices.hostdevice.HostDevice, hostdev_xml)

    def test_storage(self):
        self.assertRaises(
            NotImplementedError,
            vmdevices.storage.Drive.from_xml_tree,
            self.log,
            None,
            {}
        )

    @permutations(_STORAGE_TEST_DATA)
    def test_storage_from_xml(self, storage_xml, is_block, meta):
        with MonkeyPatchScope([
            (utils, 'isBlockDevice', lambda path: is_block)
        ]):
            dev = vmdevices.storage.Drive(
                self.log, **vmdevices.storagexml.parse(
                    vmxml.parse_xml(storage_xml),
                    {} if meta is None else meta
                )
            )
            self._check_device_xml(dev, storage_xml)

    def _check_roundtrip(self, klass, dev_xml, meta=None, expected_xml=None):
        dev = klass.from_xml_tree(
            self.log,
            vmxml.parse_xml(dev_xml),
            {} if meta is None else meta
        )
        self._check_device_xml(dev, dev_xml, expected_xml)

    def _check_device_xml(self, dev, dev_xml, expected_xml=None):
        dev.setup()
        try:
            rebuilt_xml = vmxml.format_xml(dev.getXML(), pretty=True)
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
    </disk>
  </devices>
</domain>"""


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
        root = vmxml.parse_xml(_DRIVE_PAYLOAD_XML)

        dev_xml = root.find('./devices/disk')

        with md_desc.device(devtype='disk', name='hdd') as meta:
            dev_obj = vmdevices.storage.Drive(
                self.log, **vmdevices.storagexml.parse(dev_xml, meta)
            )
            self.assertEqual(dev_obj.specParams['vmPayload'], vmPayload)

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
    <ovirt-vm:specParams />
    <ovirt-vm:vm_custom />
  </ovirt-vm:device>
</ovirt-vm:vm>'''

        md_desc = metadata.Descriptor.from_xml(_DRIVE_PAYLOAD_XML)
        self.assertXMLEqual(md_desc.to_xml(), expected_xml)


# invalid domain with only the relevant sections added
# UUID has no meaning, randomly generated
_DOMAIN_MD_MATCH_XML = u"""<domain type='kvm' id='2'>
  <uuid>dd493ddc-1ef2-4445-a248-4a7bc266a671</uuid>
  <metadata
        xmlns:ovirt-tune='http://ovirt.org/vm/tune/1.0'
        xmlns:ovirt-vm='http://ovirt.org/vm/1.0'>
    <ovirt-tune:qos/>
    <ovirt-vm:vm>
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
    </ovirt-vm:vm>
  </metadata>
  <devices>
    <emulator>/usr/libexec/qemu-kvm</emulator>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source startupPolicy='optional'/>
      <backingStore/>
      <target dev='hdc' bus='ide'/>
      <readonly/>
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
        self.assertEqual(nic.network, 'INVALID0')

    def test_match_interface_by_mac_only_succeeds(self):
        dev_objs = vmdevices.common.dev_map_from_domain_xml(
            'TESTING', self.dom_desc, self.md_desc, self.log
        )
        nic = self._find_nic_by_mac(dev_objs, '00:1a:3b:16:10:16')
        self.assertEqual(nic.network, 'ovirtmgmt1')

    def test_match_interface_by_mac_and_alias_succeeds(self):
        # mac is enough, but we match extra arguments if given
        dev_objs = vmdevices.common.dev_map_from_domain_xml(
            'TESTING', self.dom_desc, self.md_desc, self.log
        )
        nic = self._find_nic_by_mac(dev_objs, '00:1a:55:ff:20:26')
        self.assertEqual(nic.network, 'ovirtmgmt2')

    def test_port_mirroring(self):
        dev_objs = vmdevices.common.dev_map_from_domain_xml(
            'TESTING', self.dom_desc, self.md_desc, self.log
        )
        # random MAC, any nic with portMirroring configured is fine
        nic1 = self._find_nic_by_mac(dev_objs, '00:1a:55:ff:20:26')
        self.assertFalse(hasattr(nic1, 'portMirroring'))

        nic2 = self._find_nic_by_mac(dev_objs, '00:1a:4a:16:01:00')
        self.assertEqual(nic2.portMirroring, ['network1', 'network2'])

    def _find_nic_by_mac(self, dev_objs, mac_addr):
        for nic in dev_objs[vmdevices.hwclass.NIC]:
            if nic.macAddr == mac_addr:
                return nic
        raise AssertionError('no nic with mac=%s found' % mac_addr)


class FakeLibvirtConnection(object):

    def get(self, *args, **kwargs):
        return fake.Connection()
