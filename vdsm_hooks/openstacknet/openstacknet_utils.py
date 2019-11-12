#!/usr/bin/python3

from __future__ import absolute_import
from __future__ import print_function

import hooking
import os
import subprocess
from vdsm import constants
from vdsm.common.cmdutils import CommandPath
from vdsm.network.api import DUMMY_BRIDGE

# Constants for hook's API
VM_ID_KEY = 'vmId'
PROVIDER_TYPE_KEY = 'provider_type'
OPENSTACK_NET_PROVIDER_TYPE = 'OPENSTACK_NETWORK'
VNIC_ID_KEY = 'vnic_id'
PLUGIN_TYPE_KEY = 'plugin_type'
PT_BRIDGE = 'LINUX_BRIDGE'
PT_OVS = 'OPEN_VSWITCH'
PT_OPENSTACK_OVN = 'OPENSTACK_OVN'

# Default integration bridge name to use for OVS
INTEGRATION_BRIDGE = 'br-int'

# The maximum device name length in Linux
DEV_MAX_LENGTH = 14

ovs_vsctl = CommandPath('ovs-vsctl',
                        '/usr/sbin/ovs-vsctl',
                        '/usr/bin/ovs-vsctl')
MARK_FOR_UNPAUSE_FILE = 'marked_for_unpause'
MARK_FOR_UNPAUSE_PATH = os.path.join(
    constants.P_VDSM_RUN,
    '%s',
    MARK_FOR_UNPAUSE_FILE,
)

# Make pyflakes happy
DUMMY_BRIDGE


def executeOrExit(command):
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        raise RuntimeError("Failed to execute %s, due to: %s" %
                           (command, err))


def mockExecuteOrExit(command):
    print("Mocking successful execution of: %s" %
          subprocess.list2cmdline(command))
    return (0, '', '')


def devName(prefix, name):
    return (prefix + name)[:DEV_MAX_LENGTH]
