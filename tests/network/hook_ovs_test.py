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
import sys

from nose.plugins.attrib import attr

from testlib import XMLTestCase
from monkeypatch import MonkeyPatchScope

from vdsm.network import netconfpersistence

sys.path.append('../vdsm_hooks/ovs')     # Devel Location
sys.path.append('../../vdsm_hooks/ovs')  # Devel Location
sys.path.append('/usr/libexec/vdsm/')    # Deploy Location
try:
    import ovs_utils
except:
    # Assuming that the failure is due to ovs-vsctl missing (ovs not installed)
    from vdsm.utils import CommandPath
    with MonkeyPatchScope([(CommandPath, 'cmd', None)]):
        import ovs_utils
import ovs_migrate


@attr(type='unit')
class TestOvsHookMigration(XMLTestCase):

    class MockRunningConfigNoVlan:
        def __init__(self):
            self.networks = {'testnet0': {'vlan': None}}

    class MockRunningConfigVlan:
        def __init__(self):
            self.networks = {'testnet0': {'vlan': 101}}

    def test_legacy_to_legacy_non_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      to_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      is_destination_ovs=False,
                                      is_vlan_net=False,
                                      normalize=False)

    def test_legacy_to_legacy_with_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      to_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      is_destination_ovs=False,
                                      is_vlan_net=True,
                                      normalize=False)

    def test_ovs_to_ovs_non_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_OVS,
                                      to_xml=LIBVIRT_XML_DESCR_OVS,
                                      is_destination_ovs=True,
                                      is_vlan_net=False,
                                      normalize=False)

    def test_ovs_to_ovs_with_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_OVS_VLAN,
                                      to_xml=LIBVIRT_XML_DESCR_OVS_VLAN,
                                      is_destination_ovs=True,
                                      is_vlan_net=True,
                                      normalize=False)

    def test_legacy_to_ovs_non_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      to_xml=LIBVIRT_XML_DESCR_OVS,
                                      is_destination_ovs=True,
                                      is_vlan_net=False,
                                      normalize=True)

    def test_legacy_to_ovs_with_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      to_xml=LIBVIRT_XML_DESCR_OVS_VLAN,
                                      is_destination_ovs=True,
                                      is_vlan_net=True,
                                      normalize=True)

    def test_ovs_to_legacy_non_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_OVS,
                                      to_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      is_destination_ovs=False,
                                      is_vlan_net=False,
                                      normalize=True)

    def test_ovs_to_legacy_with_vlan(self):
        self._assert_device_migration(from_xml=LIBVIRT_XML_DESCR_OVS_VLAN,
                                      to_xml=LIBVIRT_XML_DESCR_LEGACY,
                                      is_destination_ovs=False,
                                      is_vlan_net=True,
                                      normalize=True)

    def _assert_device_migration(self, from_xml, to_xml, is_destination_ovs,
                                 is_vlan_net, normalize):
        stdin = BytesIO(from_xml)
        stdout = BytesIO()

        MockRunningConfig = (self.MockRunningConfigVlan if is_vlan_net else
                             self.MockRunningConfigNoVlan)
        with MonkeyPatchScope([(netconfpersistence, 'RunningConfig',
                                MockRunningConfig),
                               (ovs_utils, 'is_ovs_network',
                                lambda x: is_destination_ovs)]):
            ovs_migrate.main('do', 'migrate', 'ph', stdin=stdin, stdout=stdout)

        self.assertXMLEqual(stdout.getvalue(), to_xml)


LIBVIRT_XML_DESCR_LEGACY = """<domain type="kvm">
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
  </devices>
</domain>"""

# Note:
# libvirt automatically generates the element 'parameters' under 'virtualport'
# <virtualport type="openvswitch">
#   <parameters interfaceid="8b44132f-301f-4af5-bc16-f18f0c4c39c1" />
# </virtualport>
# When converting from legacy to ovs, the hook does not add it and leaves it up
# to libvirt to add its own defaults.
LIBVIRT_XML_DESCR_OVS = """<domain type="kvm">
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
  </devices>
</domain>"""

# Device bound to an OVS vlan network (bridge name is not OVS default)
LIBVIRT_XML_DESCR_OVS_VLAN = LIBVIRT_XML_DESCR_OVS.replace('ovsbr0',
                                                           'testnet0', 1)
