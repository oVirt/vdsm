#!/usr/bin/python

import os
import sys
import traceback

import hooking

def removeMirrorNetwork(networkName):
    '''
    this commands will remove our monitored network (bridge) from the queue:

    tc qdisc del dev networkName root
    tc qdisc del dev networkName ingress
    '''

    command = ['/sbin/tc', 'qdisc', 'del', 'dev', networkName, 'root']
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))

    command = ['/sbin/tc', 'qdisc', 'del', 'dev', networkName, 'ingress']
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))

    # remove promisc mode flag from the bridge
    command = ['/sbin/ifconfig', networkName, '-promisc']
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('promisc: error executing command "%s" error: %s' % (command, err))

if os.environ.has_key('promisc'):
    try:
        networks = os.environ['promisc']

        for networkmode in networks.split(','):
            network, mode = networkmode.split(':')
            sys.stderr.write('promisc: destroying monitoring network %s in mode %s\n' % (network, mode))
            removeMirrorNetwork(network)

    except:
        sys.stderr.write('promisc: [unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)
