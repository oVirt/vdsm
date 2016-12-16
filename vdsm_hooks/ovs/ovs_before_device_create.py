#!/usr/bin/python2
# Copyright 2015 Red Hat, Inc.
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
import sys
import traceback

from vdsm.network.netconfpersistence import RunningConfig

import hooking

from ovs_utils import is_ovs_network, BRIDGE_NAME


def ovs_device(domxml):
    """ Modify interface XML in Libvirt to proper OVS configuration if OVS
    network is set as a source.
    """
    try:
        iface = domxml.getElementsByTagName('interface')[0]
    except IndexError:
        return  # skip if not an interface
    source = iface.getElementsByTagName('source')[0]
    source_bridge = source.getAttribute('bridge')

    running_config = RunningConfig()
    network = running_config.networks.get(source_bridge)
    if network is None:
        hooking.exit_hook('Network %s does not exist' % source_bridge)

    if is_ovs_network(network):
        virtualport = domxml.createElement('virtualport')
        virtualport.setAttribute('type', 'openvswitch')
        iface.appendChild(virtualport)
        if network.get('vlan') is None:
            source.setAttribute('bridge', BRIDGE_NAME)


def main():
    domxml = hooking.read_domxml()
    ovs_device(domxml)
    hooking.write_domxml(domxml)


def test():
    def pretty_xml(xmldom):
        ugly_xml = xmldom.toprettyxml(indent='  ', encoding='utf-8')
        pretty_xml = '\n'.join(l for l in ugly_xml.split('\n') if l.strip())
        return pretty_xml

    def fake_init(self):
        self.networks = {
            'net1': {'nic': 'dummy_1', 'custom': {'ovs': True}},
            'net2': {'nic': 'dummy_2', 'vlan': 12, 'custom': {'ovs': True}},
            'net3': {'nic': 'dummy_3'}}
        self.bondings = {}
    RunningConfig.__init__ = fake_init

    from xml.dom import minidom

    # Untagged OVS network gets type=openvswitch and bridge=*OVSBridge*
    iface = minidom.parseString("""<?xml version="1.0" encoding="utf-8"?>
<interface type="bridge">
  <model type="virtio"/>
  <source bridge="net1"/>
</interface>""")
    ovs_device(iface)
    updated_device = pretty_xml(iface)
    expected_device = """<?xml version="1.0" encoding="utf-8"?>
<interface type="bridge">
  <model type="virtio"/>
  <source bridge="ovsbr0"/>
  <virtualport type="openvswitch"/>
</interface>"""
    assert updated_device == expected_device

    # Tagged OVS network gets type=openvswitch and bridge=*OVSFakeBridge*
    iface = minidom.parseString("""<?xml version="1.0" encoding="utf-8"?>
<interface type="bridge">
  <model type="virtio"/>
  <source bridge="net2"/>
</interface>""")
    ovs_device(iface)
    updated_device = pretty_xml(iface)
    expected_device = """<?xml version="1.0" encoding="utf-8"?>
<interface type="bridge">
  <model type="virtio"/>
  <source bridge="net2"/>
  <virtualport type="openvswitch"/>
</interface>"""
    assert updated_device == expected_device

    # Non-OVS is unchanged
    iface = minidom.parseString("""<?xml version="1.0" encoding="utf-8"?>
<interface type="bridge">
  <model type="virtio"/>
  <source bridge="net3"/>
</interface>""")
    ovs_device(iface)
    updated_device = pretty_xml(iface)
    expected_device = """<?xml version="1.0" encoding="utf-8"?>
<interface type="bridge">
  <model type="virtio"/>
  <source bridge="net3"/>
</interface>"""
    assert updated_device == expected_device

    # Other than vNic devices are ignored
    iface = minidom.parseString("""<?xml version="1.0" encoding="utf-8"?>
<foodevice foo="bar"/>""")
    ovs_device(iface)
    updated_device = pretty_xml(iface)
    expected_device = """<?xml version="1.0" encoding="utf-8"?>
<foodevice foo="bar"/>"""
    assert updated_device == expected_device

    print("OK")


if __name__ == '__main__':
    try:
        # Usage: PYTHONPATH=vdsm:vdsm/vdsm ./ovs_before_device_create.py -t
        if '-t' in sys.argv:
            test()
        else:
            main()
    except:
        hooking.exit_hook(traceback.format_exc())
