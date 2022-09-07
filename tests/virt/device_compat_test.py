# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.common import xmlutils
from vdsm.virt import vmdevices

from testlib import XMLTestCase


class TestDeviceCompat(XMLTestCase):

    def test_interface(self):
        dev_xml = u"""<interface type="bridge">
            <address bus="0x00" domain="0x0000"
                function="0x0" slot="0x03" type="pci"/>
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
        </interface>"""
        dev_conf = {
            'nicModel': 'pv',
            'macAddr': '52:54:00:59:F5:3F',
            'network': 'ovirtmgmt',
            'address': {
                'slot': '0x03',
                'bus': '0x00',
                'domain': '0x0000',
                'function': '0x0',
                'type': 'pci'
            },
            'device': 'bridge',
            'type': 'interface',
            'bootOrder': '1',
            'filter': 'no-mac-spoofing',
            'filterParameters': [],
            'specParams': {
                'inbound': {
                    'average': '1000',
                    'peak': '5000',
                    'burst': '1024'
                },
                'outbound': {
                    'average': '128',
                    'burst': '256'
                }
            },
            'custom': {
                'queues': '7'
            },
        }
        dev_meta = {
            'vmid': 'testvm',
            'custom': {
                'queues': '7',
            },
            'vm_custom': {
                'vhost': 'ovirtmgmt:true',
                'sndbuf': '0',
            },
        }
        self._assertDeviceCorrect(
            vmdevices.network.Interface, dev_xml, dev_conf, dev_meta
        )

    def test_drive(self):
        dev_xml = u'''<disk snapshot="no" type="block" device="disk">
            <address bus="0" controller="0" unit="0" type="drive" target="0"/>
            <source dev="/rhev/data-center/mnt/blockSD/a/images/b/c"/>
            <target dev="sda" bus="scsi"/>
            <serial>d591482b-eb24-47bd-be07-082c115d11f4</serial>
            <boot order="1"/>
            <driver name="qemu" io="native" type="qcow2"
              error_policy="stop" cache="none"/>
            <alias name="ua-58ca6050-0134-00d6-0053-000000000388"/>
        </disk>'''
        dev_conf = {
            'address': {
                'bus': '0',
                'controller': '0',
                'target': '0',
                'type': 'drive',
                'unit': '0'
            },
            'alias': 'ua-58ca6050-0134-00d6-0053-000000000388',
            'bootOrder': '1',
            'cache': 'none',
            'device': 'disk',
            'discard': 'False',
            'diskType': 'block',
            'format': 'cow',
            'iface': 'scsi',
            'index': '0',
            'name': 'sda',
            'path': '/rhev/data-center/mnt/blockSD/a/images/b/c',
            'propagateErrors': 'off',
            'serial': 'd591482b-eb24-47bd-be07-082c115d11f4',
            'specParams': {},
            'type': 'disk',
            'volumeChain': [],
            'volumeInfo': {}
        }
        dev_meta = {}
        dev = vmdevices.storage.Drive(
            self.log,
            **vmdevices.storagexml.parse(
                xmlutils.fromstring(dev_xml),
                dev_meta
            )
        )
        assert dev.config() == dev_conf

        assert dev.config() == \
            vmdevices.storage.Drive(self.log, **dev_conf).config()

    def _assertDeviceCorrect(self, dev_class, dev_xml, dev_conf, dev_meta):
        dev = dev_class.from_xml_tree(
            self.log,
            xmlutils.fromstring(dev_xml),
            dev_meta
        )
        assert dev.vmid == 'testvm'
        assert dev.config() == dev_conf

        assert dev.config() == \
            dev_class(self.log, **dev_conf).config()
