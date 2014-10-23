#!/usr/bin/env python

"""
OpenStack Network Hook (pre device migration)
=============================================
The hook receives a port_id for a migrated virtual NIC that is to be handled by
creating a security groups bridge if security groups are needed. If no security
groups are needed, the xml of the device will already refer libvirt to use ovs,
so no change is needed in that flow.

For the security groups, then, the current implementation will connect the vNIC
the tap will be connected to a dedicated Linux Bridge which will be connected
by veth pair to the OVS integration bridge. The reason for this is that
currently the Security Groups implementation (iptables) doesn't work on the OVS
bridge, so a workaround had to be taken (same as OpenStack Compute does it).

Syntax:
    { 'provider_type': 'OPENSTACK_NETWORK', 'vnic_id': 'port_id',
      'plugin_type': 'plugin_type_value', 'security_groups': .* }
Where:
    port_id should be replaced with the port id of the virtual NIC to be
    connected to OpenStack Network.
    plugin_type_value should be replaced with with OPEN_VSWITCH for OVS plugin
    or anything else for other plugins.
    security_groups will trigger the correct behavior for enabling security
    groups support, mainly when using OVS. The value is unimportant.
"""
import hooking
import os
import sys
import traceback

from openstacknet_utils import OPENSTACK_NET_PROVIDER_TYPE
from openstacknet_utils import PLUGIN_TYPE_KEY
from openstacknet_utils import PROVIDER_TYPE_KEY
from openstacknet_utils import PT_OVS
from openstacknet_utils import SECURITY_GROUPS_KEY
from openstacknet_utils import VNIC_ID_KEY
from openstacknet_utils import setUpSecurityGroupVnic


def main():
    if PROVIDER_TYPE_KEY not in os.environ:
        return

    providerType = os.environ[PROVIDER_TYPE_KEY]
    pluginType = os.environ[PLUGIN_TYPE_KEY]
    if (providerType == OPENSTACK_NET_PROVIDER_TYPE and
            pluginType == PT_OVS and SECURITY_GROUPS_KEY in os.environ):
        domxml = hooking.read_domxml()
        portId = os.environ[VNIC_ID_KEY]
        iface = domxml.getElementsByTagName('interface')[0]
        mac = iface.getElementsByTagName('mac')[0].getAttribute('address')
        setUpSecurityGroupVnic(mac, portId)


def test():
    """Should print commands for:
    - qbrtest_port_i linux bridge
    - qvbtest_port_i veth attached to the bridge above
    - qvbtest_port_i matching veth attached to the br-int with portId
      'test_port_id' and mac '00:1a:4a:16:01:51'
    """
    import openstacknet_utils
    openstacknet_utils.executeOrExit = openstacknet_utils.mockExecuteOrExit
    openstacknet_utils.deviceExists = openstacknet_utils.mockDeviceExists
    setUpSecurityGroupVnic("00:1a:4a:16:01:51", 'test_port_id')


if __name__ == '__main__':
    try:
        if '-t' in sys.argv:
            test()
        else:
            main()
    except:
        hooking.exit_hook('openstacknet hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
