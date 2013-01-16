#!/usr/bin/python

'''
OpenStack Network Hook
======================
The hook receives a port_id for a virtual NIC that is to be handled by the
OpenStack Network agent, instead of connecting it to a linux bridge.

For the Linux Bridge plugin, the current implementation will connect the
vNIC to a dummy bridge, and a post-creation hook will disconnect it so that
the agent can connect it to the correct bridge.

Syntax:
    { 'provider_type': 'OPENSTACK_NETWORK', 'vnic_id': 'port_id' }
Where:
    port_id should be replaced with the port id of the virtual NIC to be
    connected to OpenStack Network.'''

import os
import sys
import traceback
from xml.dom import minidom

import hooking
from openstacknet_consts import DEV_MAX_LENGTH
from openstacknet_consts import DUMMY_BRIDGE
from openstacknet_consts import OPENSTACK_NET_PROVIDER_TYPE
from openstacknet_consts import PROVIDER_TYPE_KEY
from openstacknet_consts import VNIC_ID_KEY

HELP_ARG = "-h"
TEST_ARG = "-t"
HELP_TEXT = """usage: %(prog)s [%(help)s] [%(test)s]

OpenStack Network Hook.

optional arguments:
  %(help)s  show this help message and exit
  %(test)s  run a dry test for the hook
""" % {'help': HELP_ARG, 'test': TEST_ARG, 'prog': sys.argv[0]}


def addOpenstackVnic(domxml, portId):
    iface = domxml.getElementsByTagName('interface')[0]
    tapName = ('tap' + portId)[:DEV_MAX_LENGTH]

    target = domxml.createElement('target')
    target.setAttribute('dev', tapName)
    iface.appendChild(target)

    source = iface.getElementsByTagName('source')[0]
    source.setAttribute('bridge', DUMMY_BRIDGE)


def main():
    if PROVIDER_TYPE_KEY not in os.environ:
        return

    providerType = os.environ[PROVIDER_TYPE_KEY]
    if providerType == OPENSTACK_NET_PROVIDER_TYPE:
        domxml = hooking.read_domxml()
        vNicId = os.environ[VNIC_ID_KEY]
        sys.stderr.write('Adding vNIC %s for provider type %s'
                         % (vNicId, providerType))
        addOpenstackVnic(domxml, vNicId)
        hooking.write_domxml(domxml)


def test():
    domxml = minidom.parseString("""<?xml version="1.0" encoding="utf-8"?>
    <interface type="bridge">
        <mac address="00:1a:4a:16:01:51"/>
        <model type="virtio"/>
        <source bridge="sample_network"/>
    </interface>""")
    addOpenstackVnic(domxml, 'test_port_id')
    print domxml.toxml(encoding='utf-8')


if __name__ == '__main__':
    if HELP_ARG in sys.argv:
        hooking.exit_hook(HELP_TEXT)

    try:
        if TEST_ARG in sys.argv:
            test()
        else:
            main()
    except:
        hooking.exit_hook('openstacknet hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
