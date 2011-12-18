#!/usr/bin/python

import os
import sys
import utils
import hooking
import traceback

MODE_MIRROR = 'mirror'
MODE_INLINE = 'redirect'

'''
promisc vdsm hook
=================
hook is getting network (bridge) name and mode: prmisc=blue:mirror,red:redirect and
set the current running vm in promiscuous mode, ie: mirror all blue traffic to current vm

syntax:
1. promisc=blue:mirror
    # mirror monitoring the network blue (all traffic will goto the VMs interface and the network)
2. promisc=blue:redirect
    # redirect network blue traffic to VMs interface (all traffic will goto the VMs interface,
    # and the its the VM responsibility to redirect the traffic back to blues interfaces)
'''

def getIfaceName(iface):
    target = iface.getElementsByTagName('target')[0]
    return target.attributes['dev'].value


def captureNetwork(networkName, ifaceName, mode):
    '''
    this commands mirror all networkName traffic to ifaceName:

    $ tc qdisc add dev networkName ingress
    $ tc filter add dev networkName parent ffff: protocol ip \
            u32 match u8 0 0 action mirred egress mirror dev ifaceName
    $ tc qdisc replace dev networkName parent root prio

    get the id and set it as the parent id of the next commad
    id=`tc qdisc show dev networkName | grep prio | awk '{print $3}'`

    # set the parent id
    tc filter add dev networkName parent $id protocol ip \
            u32 match u8 0 0 action mirred egress mirror dev ifaceName

    NOTE:
    =====
    1. use redirect instead of mirror for in-line mode (ie dont copy the packets
        forward it to ifaceName and he will redirect them)
    2. redirect (not mirror) with ebtables:
        need to change the mac address of the packets from monitored interface to
        the monitoring interface. (the ip stay the same, so this way you know that the
        packets are not meant to the monitoring machine).

        set the bridge in promisc mode
        $ ifconfig <netwok name> promisc
        traffic to the monitoring machine
        $ ebtables -t nat -A PREROUTING -d 00:1a:4a:16:01:51 -i eth0 -j dnat --to-destination 00:1a:4a:16:01:11
        traffic from the monitoring machine
        $ ebtables -t nat -A PREROUTING -s 00:1a:4a:16:01:51 -i vnet0 -j dnat --to-destination 00:1a:4a:16:01:11
    '''

    command = ['/sbin/tc', 'qdisc', 'add', 'dev', networkName, 'ingress']
    retcode, out, err = utils.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))
        sys.exit(2)

    command = ['/sbin/tc', 'filter', 'add', 'dev', networkName, 'parent', 'ffff:', 'protocol',
               'ip', 'u32', 'match', 'u8', '0', '0', 'action', 'mirred', 'egress', mode,
               'dev', ifaceName]
    retcode, out, err = utils.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))
        sys.exit(2)

    command = ['/sbin/tc', 'qdisc', 'replace', 'dev', networkName, 'parent', 'root', 'prio']
    retcode, out, err = utils.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))
        sys.exit(2)

    command = ['/sbin/tc', 'qdisc', 'show', 'dev', networkName]
    retcode, out, err = utils.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))
        sys.exit(2)

    #TODO: change string slicing to regex
    devId = out[11:16]
    sys.stderr.write('promisc: filtering devId=%s\n' % devId)

    command = ['/sbin/tc', 'filter', 'add', 'dev', networkName, 'parent', devId, 'protocol',
               'ip', 'u32', 'match', 'u8', '0', '0', 'action', 'mirred', 'egress', mode, 'dev', ifaceName]
    retcode, out, err = utils.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))
        sys.exit(2)

    # add promisc mode to the bridge
    command = ['/sbin/ifconfig', networkName, 'promisc']
    retcode, out, err = utils.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))
        sys.exit(2)

if os.environ.has_key('promisc'):
    try:
        networks = os.environ['promisc']

        domxml = hooking.read_domxml()
        interfaces = domxml.getElementsByTagName('interface')

        for networkmode in networks.split(','):
            network, mode = networkmode.split(':')
            sys.stderr.write('promisc: monitoring network %s in mode %s\n' % (network, mode))

            if mode != MODE_MIRROR and mode != MODE_INLINE:
                sys.stderr.write('promisc: mode error %s - can only be %s or %s\n' % (networkmode, MODE_MIRROR, MODE_INLINE))
                sys.exit(2)

            for iface in interfaces:
                if iface.hasAttribute('type') and iface.attributes['type'].value == 'bridge':
                    ifaceName = getIfaceName(iface)
                    captureNetwork(network, ifaceName, mode)

    except:
        sys.stderr.write('promisc: [unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)
