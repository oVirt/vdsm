#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

'''
OpenStack Network Hook (Post device creation)
=============================================
The hook receives a port_id for a virtual NIC that is to be handled by the
OpenStack Network agent, instead of connecting it to a linux bridge.

The purpose of the post creation hook is to disconnect the tap from the dummy
bridge, allowing the Linux Bridge agent to connect it to the correct bridge.

Note: The Linux Bridge agent will try to connect the tap when it finds it.
Should it fail (due to a race condition, when this hook hasn't run yet) it
will simply retry in a couple of seconds.
In this regard, there is no concern of "raceful" behavior between this hook
and the Linux Bridge agent.

Syntax:
    { 'provider_type': 'OPENSTACK_NETWORK', 'vnic_id': 'port_id' }
Where:
    port_id should be replaced with the port id of the virtual NIC to be
    connected to OpenStack Network.'''

from __future__ import absolute_import

import os
import sys
import traceback

import hooking
from openstacknet_utils import DUMMY_BRIDGE
from openstacknet_utils import OPENSTACK_NET_PROVIDER_TYPE
from openstacknet_utils import PLUGIN_TYPE_KEY
from openstacknet_utils import PROVIDER_TYPE_KEY
from openstacknet_utils import PT_BRIDGE
from openstacknet_utils import VNIC_ID_KEY
from openstacknet_utils import devName
from openstacknet_utils import executeOrExit


def disconnectVnic(portId):
    executeOrExit([
        '/usr/sbin/ip', 'link', 'set', devName('tap', portId), 'nomaster'])


def main():
    if PROVIDER_TYPE_KEY not in os.environ:
        return

    providerType = os.environ[PROVIDER_TYPE_KEY]
    pluginType = os.environ[PLUGIN_TYPE_KEY]
    if (providerType == OPENSTACK_NET_PROVIDER_TYPE
            and pluginType == PT_BRIDGE):
        vNicId = os.environ[VNIC_ID_KEY]
        sys.stderr.write('Removing vNIC %s from %s for provider type %s'
                         % (vNicId, DUMMY_BRIDGE, providerType))
        disconnectVnic(vNicId)


if __name__ == '__main__':
    try:
        main()
    except:
        hooking.exit_hook('openstacknet hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
