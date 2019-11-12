#!/usr/bin/python3

'''
OpenStack Network Hook
======================
The hook receives a port_id for a virtual NIC that is to be handled by the
OpenStack Network agent, instead of connecting it to a linux bridge.

For the Linux Bridge plugin, the current implementation will connect the
vNIC to a dummy bridge, and a post-creation hook will disconnect it so that
the agent can connect it to the correct bridge.

For the OVS plugin, the current implementation will connect the vNIC to the
default integration bridge, named "br_int".

Syntax:
    { 'provider_type': 'OPENSTACK_NETWORK', 'vnic_id': 'port_id',
      'plugin_type': 'plugin_type_value' }
Where:
    port_id should be replaced with the port id of the virtual NIC to be
    connected to OpenStack Network.
    plugin_type_value should be replaced with with OPEN_VSWITCH for OVS plugin
    of anything else for Linux Bridge plugin.
'''
from __future__ import absolute_import
from __future__ import print_function

import libvirt
import os
import sys
import traceback
from xml.dom import minidom

import hooking
from openstacknet_utils import DUMMY_BRIDGE
from openstacknet_utils import INTEGRATION_BRIDGE
from openstacknet_utils import MARK_FOR_UNPAUSE_PATH
from openstacknet_utils import OPENSTACK_NET_PROVIDER_TYPE
from openstacknet_utils import PLUGIN_TYPE_KEY
from openstacknet_utils import PROVIDER_TYPE_KEY
from openstacknet_utils import PT_BRIDGE
from openstacknet_utils import PT_OPENSTACK_OVN
from openstacknet_utils import PT_OVS
from openstacknet_utils import VM_ID_KEY
from openstacknet_utils import VNIC_ID_KEY
from openstacknet_utils import devName

HELP_ARG = "-h"
TEST_ARG = "-t"
OVS_ARG = "-o"
HELP_TEXT = """usage: %(prog)s [%(help)s] [%(test)s [%(ovs)s]]

OpenStack Network Hook.

optional arguments:
  %(help)s  show this help message and exit
  %(test)s  run a dry test for the hook
  %(ovs)s  run the test with OVS
""" % {'help': HELP_ARG,
       'test': TEST_ARG,
       'ovs': OVS_ARG,
       'prog': sys.argv[0]}


def defineLinuxBridge(domxml, iface, portId, brName):
    target = domxml.createElement('target')
    target.setAttribute('dev', devName('tap', portId))
    iface.appendChild(target)

    source = iface.getElementsByTagName('source')[0]
    source.setAttribute('bridge', brName)


def addLinuxBridgeVnic(domxml, iface, portId):
    defineLinuxBridge(domxml, iface, portId, DUMMY_BRIDGE)


def addOvsVnic(domxml, iface, portId):
    addOvsDirectVnic(domxml, iface, portId)


def addOvsDirectVnic(domxml, iface, portId):
    defineLinuxBridge(domxml, iface, portId, INTEGRATION_BRIDGE)

    virtualPort = domxml.createElement('virtualport')
    virtualPort.setAttribute('type', 'openvswitch')
    virtualPortParameters = domxml.createElement('parameters')
    virtualPortParameters.setAttribute('interfaceid', portId)
    virtualPort.appendChild(virtualPortParameters)
    iface.appendChild(virtualPort)


def addOpenstackVnic(domxml, pluginType, portId):
    iface = domxml.getElementsByTagName('interface')[0]
    if pluginType == PT_BRIDGE:
        addLinuxBridgeVnic(domxml, iface, portId)
    elif pluginType in (PT_OVS, PT_OPENSTACK_OVN):
        addOvsVnic(domxml, iface, portId)
    else:
        hooking.exit_hook("Unknown plugin type: %s" % pluginType)


def mark_for_unpause(vm_id):
    unpause_file = MARK_FOR_UNPAUSE_PATH % vm_id
    try:
        os.makedirs(os.path.dirname(unpause_file))
    except OSError:
        pass
    with open(unpause_file, mode='w') as f:
        f.write("true")


def main():
    if PROVIDER_TYPE_KEY not in os.environ:
        return

    providerType = os.environ[PROVIDER_TYPE_KEY]
    if providerType == OPENSTACK_NET_PROVIDER_TYPE:
        domxml = hooking.read_domxml()
        vNicId = os.environ[VNIC_ID_KEY]
        pluginType = os.environ[PLUGIN_TYPE_KEY]
        sys.stderr.write('Adding vNIC %s for provider type %s and plugin %s'
                         % (vNicId, providerType, pluginType))
        addOpenstackVnic(domxml, pluginType, vNicId)
        hooking.write_domxml(domxml)

        vm_id = os.environ[VM_ID_KEY]
        PAUSE_FLAG = libvirt.VIR_DOMAIN_START_PAUSED
        flags = hooking.load_vm_launch_flags_from_file(vm_id)

        if (flags & PAUSE_FLAG) != PAUSE_FLAG:
            flags |= PAUSE_FLAG
            hooking.dump_vm_launch_flags_to_file(vm_id, flags)
            mark_for_unpause(vm_id)


def test(ovs):
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

    import openstacknet_utils
    openstacknet_utils.executeOrExit = openstacknet_utils.mockExecuteOrExit
    addOpenstackVnic(domxml,
                     pluginType,
                     'test_port_id')
    print(domxml.toxml(encoding='utf-8'))


if __name__ == '__main__':
    if HELP_ARG in sys.argv:
        hooking.exit_hook(HELP_TEXT)

    try:
        if TEST_ARG in sys.argv:
            useOvs = OVS_ARG in sys.argv
            test(useOvs)
        else:
            main()
    except:
        hooking.exit_hook('openstacknet hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
