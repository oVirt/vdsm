#!/usr/bin/python

'''
OpenStack Network Hook
======================
The hook receives a port_id for a virtual NIC that is to be handled by the
OpenStack Network agent, instead of connecting it to a linux bridge.

For the Linux Bridge plugin, the current implementation will connect the
vNIC to a dummy bridge, and a post-creation hook will disconnect it so that
the agent can connect it to the correct bridge.

For the OVS plugin, the current implementation will connect the vNIC to the
default integration bridge, named "br_int" if no Security Groups are required.
In case Securtiy Groups are required, the tap will be connected to a dedicated
Linux Bridge which will be connected by veth pair to the OVS integration
bridge. The reason for this is that currently the Security Groups
implementation (iptables) doesn't work on the OVS bridge, so a workaround had
to be taken (same as OpenStack Compute does it).

Syntax:
    { 'provider_type': 'OPENSTACK_NETWORK', 'vnic_id': 'port_id',
      'plugin_type': 'plugin_type_value', 'security_groups': .* }
Where:
    port_id should be replaced with the port id of the virtual NIC to be
    connected to OpenStack Network.
    plugin_type_value should be replaced with with OPEN_VSWITCH for OVS plugin
    of anything else for Linux Bridge plugin.
    security_groups if present, will trigger correct behavior for enabling
    security groups support, mainly when using OVS. The value is unimportant.
'''

import os
import subprocess
import sys
import traceback
from xml.dom import minidom

import hooking
from openstacknet_utils import DUMMY_BRIDGE
from openstacknet_utils import EXT_BRCTL
from openstacknet_utils import EXT_IP
from openstacknet_utils import EXT_OVS_VSCTL
from openstacknet_utils import INTEGRATION_BRIDGE
from openstacknet_utils import OPENSTACK_NET_PROVIDER_TYPE
from openstacknet_utils import PLUGIN_TYPE_KEY
from openstacknet_utils import PROVIDER_TYPE_KEY
from openstacknet_utils import PT_BRIDGE
from openstacknet_utils import PT_OVS
from openstacknet_utils import SECURITY_GROUPS_KEY
from openstacknet_utils import VNIC_ID_KEY
from openstacknet_utils import deviceExists
from openstacknet_utils import devName
from openstacknet_utils import executeOrExit

HELP_ARG = "-h"
TEST_ARG = "-t"
OVS_ARG = "-o"
SECGROUPS_ARG = "-s"
HELP_TEXT = """usage: %(prog)s [%(help)s] [%(test)s [%(ovs)s]]

OpenStack Network Hook.

optional arguments:
  %(help)s  show this help message and exit
  %(test)s  run a dry test for the hook
  %(ovs)s  run the test with OVS
  %(secgroups)s  run the test with security groups
""" % {'help': HELP_ARG,
       'test': TEST_ARG,
       'ovs': OVS_ARG,
       'secgroups': SECGROUPS_ARG,
       'prog': sys.argv[0]}


def defineLinuxBridge(domxml, iface, portId, brName):
    target = domxml.createElement('target')
    target.setAttribute('dev', devName('tap', portId))
    iface.appendChild(target)

    source = iface.getElementsByTagName('source')[0]
    source.setAttribute('bridge', brName)


def addLinuxBridgeVnic(domxml, iface, portId):
    defineLinuxBridge(domxml, iface, portId, DUMMY_BRIDGE)


def addOvsVnic(domxml, iface, portId, hasSecurityGroups):
    if hasSecurityGroups:
        addOvsHybridVnic(domxml, iface, portId)
    else:
        addOvsDirectVnic(domxml, iface, portId)


def addOvsHybridVnic(domxml, iface, portId):
    brName = devName("qbr", portId)

    # TODO: Remove this check after bz 1045626 is fixed
    if not deviceExists(brName):
        executeOrExit([EXT_BRCTL, 'addbr', brName])
        executeOrExit([EXT_BRCTL, 'setfd', brName, '0'])
        executeOrExit([EXT_BRCTL, 'stp', brName, 'off'])

    vethBr = devName("qvb", portId)
    vethOvs = devName("qvo", portId)

    # TODO: Remove this check after bz 1045626 is fixed
    if not deviceExists(vethOvs):
        executeOrExit([EXT_IP, 'link', 'add', vethBr, 'type', 'veth', 'peer',
                      'name', vethOvs])
        for dev in [vethBr, vethOvs]:
            executeOrExit([EXT_IP, 'link', 'set', dev, 'up'])
            executeOrExit([EXT_IP, 'link', 'set', dev, 'promisc', 'on'])

        executeOrExit([EXT_IP, 'link', 'set', brName, 'up'])
        executeOrExit([EXT_BRCTL, 'addif', brName, vethBr])

        mac = iface.getElementsByTagName('mac')[0].getAttribute('address')
        executeOrExit([EXT_OVS_VSCTL, '--', '--may-exist', 'add-port',
                      INTEGRATION_BRIDGE, vethOvs,
                      '--', 'set', 'Interface', vethOvs,
                      'external-ids:iface-id=%s' % portId,
                      'external-ids:iface-status=active',
                      'external-ids:attached-mac=%s' % mac])

    defineLinuxBridge(domxml, iface, portId, brName)


def addOvsDirectVnic(domxml, iface, portId):
    source = iface.getElementsByTagName('source')[0]
    source.setAttribute('bridge', INTEGRATION_BRIDGE)

    virtualPort = domxml.createElement('virtualport')
    virtualPort.setAttribute('type', 'openvswitch')
    virtualPortParameters = domxml.createElement('parameters')
    virtualPortParameters.setAttribute('interfaceid', portId)
    virtualPort.appendChild(virtualPortParameters)
    iface.appendChild(virtualPort)


def addOpenstackVnic(domxml, pluginType, portId, hasSecurityGroups):
    iface = domxml.getElementsByTagName('interface')[0]
    if pluginType == PT_BRIDGE:
        addLinuxBridgeVnic(domxml, iface, portId)
    elif pluginType == PT_OVS:
        addOvsVnic(domxml, iface, portId, hasSecurityGroups)
    else:
        hooking.exit_hook("Unknown plugin type: %s" % pluginType)


def main():
    if PROVIDER_TYPE_KEY not in os.environ:
        return

    providerType = os.environ[PROVIDER_TYPE_KEY]
    if providerType == OPENSTACK_NET_PROVIDER_TYPE:
        domxml = hooking.read_domxml()
        vNicId = os.environ[VNIC_ID_KEY]
        pluginType = os.environ[PLUGIN_TYPE_KEY]
        hasSecurityGroups = SECURITY_GROUPS_KEY in os.environ
        sys.stderr.write('Adding vNIC %s for provider type %s and plugin %s'
                         % (vNicId, providerType, pluginType))
        addOpenstackVnic(domxml, pluginType, vNicId, hasSecurityGroups)
        hooking.write_domxml(domxml)


def mockExecuteOrExit(command):
    print ("Mocking successful execution of: %s"
           % subprocess.list2cmdline(command))
    return (0, '', '')


def mockDeviceExists(dev):
    return False


def test(ovs, withSecurityGroups):
    domxml = minidom.parseString("""<?xml version="1.0" encoding="utf-8"?>
    <interface type="bridge">
        <mac address="00:1a:4a:16:01:51"/>
        <model type="virtio"/>
        <source bridge="sample_network"/>
    </interface>""")

    if ovs:
        pluginType = PT_OVS
    else:
        pluginType = PT_BRIDGE

    globals()['executeOrExit'] = mockExecuteOrExit
    globals()['deviceExists'] = mockDeviceExists
    addOpenstackVnic(domxml,
                     pluginType,
                     'test_port_id',
                     withSecurityGroups)
    print domxml.toxml(encoding='utf-8')


if __name__ == '__main__':
    if HELP_ARG in sys.argv:
        hooking.exit_hook(HELP_TEXT)

    try:
        if TEST_ARG in sys.argv:
            useOvs = OVS_ARG in sys.argv
            useSecGroups = SECGROUPS_ARG in sys.argv
            test(useOvs, useSecGroups)
        else:
            main()
    except:
        hooking.exit_hook('openstacknet hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
