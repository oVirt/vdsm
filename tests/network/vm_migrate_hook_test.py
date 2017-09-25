# Copyright 2016 Red Hat, Inc.
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

from io import BytesIO

from nose.plugins.attrib import attr

from testlib import mock
from testlib import XMLTestCase

from vdsm.virt import vm_migrate_hook


class MockVdscli(object):
    @staticmethod
    def fullList(*args, **kwargs):
        return {'items': [VM_FULL_LIST]}


class MockJsonrpcvdscli(object):
    @staticmethod
    def connect(q):
        return MockVdscli()


@attr(type='unit')
class TestOvsHookMigration(XMLTestCase):

    def test_legacy_to_legacy_non_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      to_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      is_dest_ovs=False,
                                      is_vlan_net=False)

    def test_legacy_to_legacy_with_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      to_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      is_dest_ovs=False,
                                      is_vlan_net=True)

    def test_ovs_to_ovs_non_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_OVS,
                                      to_xml=LIBVIRT_XML_DESCR_OVS,
                                      is_dest_ovs=True,
                                      is_vlan_net=False)

    def test_ovs_to_ovs_with_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_OVS_VLAN,
                                      to_xml=LIBVIRT_XML_DESCR_OVS_VLAN,
                                      is_dest_ovs=True,
                                      is_vlan_net=True)

    def test_legacy_to_ovs_non_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      to_xml=LIBVIRT_XML_DESCR_OVS,
                                      is_dest_ovs=True,
                                      is_vlan_net=False)

    def test_legacy_to_ovs_with_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      to_xml=LIBVIRT_XML_DESCR_OVS_VLAN,
                                      is_dest_ovs=True,
                                      is_vlan_net=True)

    def test_ovs_to_legacy_non_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_OVS,
                                      to_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      is_dest_ovs=False,
                                      is_vlan_net=False)

    def test_ovs_to_legacy_with_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_OVS_VLAN,
                                      to_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      is_dest_ovs=False,
                                      is_vlan_net=True)

    # mock migration_ovs_hook_enabled
    @mock.patch('vdsm.config.config.get', lambda *x: 'true')
    @mock.patch.object(vm_migrate_hook, 'jsonrpcvdscli', MockJsonrpcvdscli)
    @mock.patch.object(vm_migrate_hook.net_api, 'net2vlan')
    @mock.patch.object(vm_migrate_hook.net_api, 'ovs_bridge')
    def _assert_device_migration(self, mock_ovs_bridge, mock_net2vlan,
                                 from_xml, to_xml, is_dest_ovs, is_vlan_net):

        mock_ovs_bridge.return_value = 'ovsbr0' if is_dest_ovs else None
        mock_net2vlan.return_value = 101 if is_vlan_net else None

        stdin = BytesIO(from_xml)
        stdout = BytesIO()
        vm_migrate_hook.main('do', 'migrate', 'ph', stdin=stdin, stdout=stdout)

        self.assertXMLEqual(stdout.getvalue(), to_xml)


LIBVIRT_XML_DESCR_LEGACY = b"""<domain type="kvm">
  <uuid>93acdb7b-5068-4966-9fc0-9e43c34bac1b</uuid>
  <devices>
    <interface type="bridge">
      <mac address="00:1a:4a:16:01:54" />
      <source bridge="testnet0" />
      <model type="virtio" />
      <filterref filter="vdsm-no-mac-spoofing" />
      <link state="up" />
      <boot order="1" />
      <address bus="0x00" domain="0x0" function="0x0" slot="0x03" type="pci" />
    </interface>
    <graphics autoport="yes" type="spice">
      <listen network="vdsm-ovirtmgmt" type="network" />
    </graphics>
  </devices>
</domain>"""

# Note:
# libvirt automatically generates the element 'parameters' under 'virtualport'
# <virtualport type="openvswitch">
#   <parameters interfaceid="8b44132f-301f-4af5-bc16-f18f0c4c39c1" />
# </virtualport>
# The hook does not add it and leaves it up to libvirt to add its own defaults.
LIBVIRT_XML_DESCR_OVS = b"""<domain type="kvm">
  <uuid>93acdb7b-5068-4966-9fc0-9e43c34bac1b</uuid>
  <devices>
    <interface type="bridge">
      <mac address="00:1a:4a:16:01:54" />
      <source bridge="ovsbr0" />
      <model type="virtio" />
      <filterref filter="vdsm-no-mac-spoofing" />
      <link state="up" />
      <boot order="1" />
      <address bus="0x00" domain="0x0" function="0x0" slot="0x03" type="pci" />
      <virtualport type="openvswitch" />
    </interface>
    <graphics autoport="yes" type="spice">
      <listen network="vdsm-ovirtmgmt" type="network" />
    </graphics>
  </devices>
</domain>"""

# Device bound to an OVS vlan network
XML_OVS_VLAN = b"""      <virtualport type="openvswitch" />
      <vlan>
        <tag id='101'/>
      </vlan>"""

LIBVIRT_XML_DESCR_OVS_VLAN = LIBVIRT_XML_DESCR_OVS.replace(
    b'      <virtualport type="openvswitch" />', XML_OVS_VLAN, 1)

VM_FULL_LIST = {
    'devices': [
        {'device': 'spice',
         'deviceId': '5ed96a21-ac34-463f-8e0e-ce6e65d320c3',
         'specParams': {'copyPasteEnable': 'true',
                        'displayIp': '10.35.160.53',
                        'displayNetwork': 'ovirtmgmt',
                        'fileTransferEnable': 'true',
                        'spiceSecureChannels':
                            'smain,sinputs,scursor,splayback,'
                            'srecord,sdisplay,ssmartcard,susbredir',
                        'spiceSslCipherSuite': 'DEFAULT'},
         'tlsPort': '5900',
         'type': 'graphics'},
        {'address': {'bus': '0x00',
                     'domain': '0x0000',
                     'function': '0x0',
                     'slot': '0x08',
                     'type': 'pci'},
         'alias': 'net0',
         'bootOrder': '2',
         'device': 'bridge',
         'deviceId': '335be770-9133-41eb-aeb1-5104dab1dc69',
         'filter': 'vdsm-no-mac-spoofing',
         'linkActive': True,
         'macAddr': '00:1a:4a:16:01:54',
         'name': 'vnet0',
         'network': 'testnet0',
         'nicModel': 'pv',
         'specParams': {'inbound': {}, 'outbound': {}},
         'type': 'interface'}],
    'display': 'qxl',
    'displayIp': '111.35.160.53',
    'displayNetwork': 'ovirtmgmt123',
    'displayPort': '-1',
    'displaySecurePort': '5900',
    'vmId': '93acdb7b-5068-4966-9fc0-9e43c34bac1b',
    'vmName': 'vm2_sr-iov',
    'vmType': 'kvm'}
