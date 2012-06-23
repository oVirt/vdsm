#!/usr/bin/python

import os
import sys
import traceback

import hooking

MODE_MIRROR = 'mirror'
MODE_INLINE = 'redirect'

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
    in in-line mode we don't filter a network
    the network parameter here is a tap device for the
    security vm
    '''

    command = ['/sbin/tc', 'qdisc', 'add', 'dev', networkName, 'ingress']
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))
        sys.exit(2)

    command = ['/sbin/tc', 'filter', 'add', 'dev', networkName, 'parent', 'ffff:', 'protocol',
               'ip', 'u32', 'match', 'u8', '0', '0', 'action', 'mirred', 'egress', mode,
               'dev', ifaceName]
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))
        sys.exit(2)

    command = ['/sbin/tc', 'qdisc', 'replace', 'dev', networkName, 'parent', 'root', 'prio']
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))
        sys.exit(2)

    command = ['/sbin/tc', 'qdisc', 'show', 'dev', networkName]
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))
        sys.exit(2)

    #TODO: change string slicing to regex
    devId = out[11:16]
    sys.stderr.write('promisc: filtering devId=%s\n' % devId)

    command = ['/sbin/tc', 'filter', 'add', 'dev', networkName, 'parent', devId, 'protocol',
               'ip', 'u32', 'match', 'u8', '0', '0', 'action', 'mirred', 'egress', mode, 'dev', ifaceName]
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))
        sys.exit(2)

    # add promisc mode to the bridge
    command = ['/sbin/ifconfig', networkName, 'promisc']
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
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
                    if mode == MODE_MIRROR:
                        captureNetwork(network, ifaceName, mode)
                    else:
                        #NOTE:
                        #in in-line mode we don't filter a network
                        #the network parameter here is a tap device for the
                        #security vm, so we switch the ifaceName and network
                        #parameter order
                        #TODO: it may be right to use the mirror as we do with the
                        #in-line mode now, ie not filter the network but filter
                        #the vm interface
                        captureNetwork(ifaceName, network, mode)

    except:
        sys.stderr.write('promisc: [unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)
