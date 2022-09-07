#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import print_function
import os
import random

from xml.dom import minidom
import sys
import traceback

import hooking
AVAIL_NETS_KEY = 'equivnets'
HELP_ARG = "-h"
TEST_ARG = "-t"
HELP_TEXT = """usage: %(prog)s [%(help)s] [%(test)s]

vm network allocation Hook.

optional arguments:
  %(help)s  show this help message and exit
  %(test)s  run a dry test for the hook
""" % {'help': HELP_ARG,
       'test': TEST_ARG,
       'prog': sys.argv[0]}


def _parse_nets():
    return [net for net in os.environ[AVAIL_NETS_KEY].split()]


def _get_random_network():
    available_nets = _parse_nets()
    if not available_nets:
        raise Exception('Found no available networks to choose from')
    return random.choice(available_nets)


def _change_assigned_network(interface, net):
    for source in interface.getElementsByTagName('source'):
        source.attributes.item(0).value = net


def allocate_random_network(interface):
    net = _get_random_network()
    _change_assigned_network(interface, net)
    hooking.log('allocating random network: %s' % (net,))


def test():
    os.environ[AVAIL_NETS_KEY] = ' special_net other_net  '

    interface = minidom.parseString("""
    <interface type="bridge">
        <address bus="0x00" domain="0x0000" function="0x0" slot="0x03"\
                                            type="pci"/>
        <mac address="00:1a:4a:16:01:b0"/>
        <model type="virtio"/>
        <source bridge="ovirtmgmt"/>
        <filterref filter="vdsm-no-mac-spoofing"/>
        <link state="up"/>
        <boot order="1"/>
    </interface>
    """).getElementsByTagName('interface')[0]

    print("Interface before removing filter: %s" %
          interface.toxml(encoding='UTF-8'))

    allocate_random_network(interface)
    print("Interface after removing filter: %s" %
          interface.toxml(encoding='UTF-8'))


def main():
    device_xml = hooking.read_domxml()
    allocate_random_network(device_xml)
    hooking.write_domxml(device_xml)


if __name__ == '__main__':
    if HELP_ARG in sys.argv:
        hooking.exit_hook(HELP_TEXT)

    try:
        if TEST_ARG in sys.argv:
            test()
        else:
            main()
    except:
        hooking.exit_hook('vm net allocation hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
