#!/usr/bin/python

import hooking
import subprocess
from vdsm.netinfo import DUMMY_BRIDGE
from vdsm.utils import CommandPath

# Constants for hook's API
PROVIDER_TYPE_KEY = 'provider_type'
OPENSTACK_NET_PROVIDER_TYPE = 'OPENSTACK_NETWORK'
VNIC_ID_KEY = 'vnic_id'
PLUGIN_TYPE_KEY = 'plugin_type'
SECURITY_GROUPS_KEY = 'security_groups'
PT_BRIDGE = 'LINUX_BRIDGE'
PT_OVS = 'OPEN_VSWITCH'

# Default integration bridge name to use for OVS
INTEGRATION_BRIDGE = 'br-int'

# The maximum device name length in Linux
DEV_MAX_LENGTH = 14

EXT_BRCTL = CommandPath('brctl', '/sbin/brctl', '/usr/sbin/brctl').cmd
EXT_IP = CommandPath('ip', '/sbin/ip').cmd
ovs_vsctl = CommandPath('ovs-vsctl',
                        '/usr/sbin/ovs-vsctl',
                        '/usr/bin/ovs-vsctl')

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


def deviceExists(dev):
    command = [EXT_IP, 'link', 'show', 'dev', dev]
    retcode, out, err = hooking.execCmd(command, raw=True)
    return retcode == 0


def mockDeviceExists(dev):
    return False


def setUpSecurityGroupVnic(macAddr, portId):
    hooking.log('Setting up vNIC (portId %s) security groups' % portId)
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

        executeOrExit([ovs_vsctl.cmd, '--', '--may-exist', 'add-port',
                       INTEGRATION_BRIDGE, vethOvs,
                       '--', 'set', 'Interface', vethOvs,
                       'external-ids:iface-id=%s' % portId,
                       'external-ids:iface-status=active',
                       'external-ids:attached-mac=%s' % macAddr])
