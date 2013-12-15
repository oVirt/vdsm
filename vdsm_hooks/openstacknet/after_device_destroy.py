#!/usr/bin/python

'''
OpenStack Network Hook (Post device destruction)
================================================
The hook receives a port_id for a virtual NIC that needs to be disconnected
from the hybrid OVS/Linux Bridge which is used to allow security groups in OVS.

Syntax:
    { 'provider_type': 'OPENSTACK_NETWORK', 'vnic_id': 'port_id' }
Where:
    port_id should be replaced with the port id of the virtual NIC to be
    disconnected from the hybrid bridge.'''

import os
import sys
import traceback

import hooking
from openstacknet_utils import EXT_BRCTL
from openstacknet_utils import EXT_IP
from openstacknet_utils import EXT_OVS_VSCTL
from openstacknet_utils import INTEGRATION_BRIDGE
from openstacknet_utils import OPENSTACK_NET_PROVIDER_TYPE
from openstacknet_utils import PLUGIN_TYPE_KEY
from openstacknet_utils import PROVIDER_TYPE_KEY
from openstacknet_utils import PT_OVS
from openstacknet_utils import VNIC_ID_KEY
from openstacknet_utils import deviceExists
from openstacknet_utils import devName
from openstacknet_utils import executeOrExit


def disconnectVnic(portId):
    brName = devName("qbr", portId)
    vethBr = devName("qvb", portId)
    vethOvs = devName("qvo", portId)

    if deviceExists(brName):
        executeOrExit([EXT_BRCTL, 'delif', brName, vethBr])
        executeOrExit([EXT_IP, 'link', 'set', brName, 'down'])
        executeOrExit([EXT_BRCTL, 'delbr', brName])
        executeOrExit([EXT_OVS_VSCTL, 'del-port', INTEGRATION_BRIDGE, vethOvs])
        executeOrExit([EXT_IP, 'link', 'delete', vethOvs])


def main():
    if PROVIDER_TYPE_KEY not in os.environ:
        return

    providerType = os.environ[PROVIDER_TYPE_KEY]
    pluginType = os.environ[PLUGIN_TYPE_KEY]
    if (providerType == OPENSTACK_NET_PROVIDER_TYPE and
            pluginType == PT_OVS):
        vNicId = os.environ[VNIC_ID_KEY]
        sys.stderr.write('Removing vNIC %s from %s for provider type %s'
                         % (vNicId, INTEGRATION_BRIDGE, providerType))
        disconnectVnic(vNicId)


if __name__ == '__main__':
    try:
        main()
    except:
        hooking.exit_hook('openstacknet hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
