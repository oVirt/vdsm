#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Tweak an interface defintion so that its source is forced to be a specified
network. Applied on a per vnic basis, it gets triggered and used by two
different events:
    * before_device_create
    * before_nic_hotplug

This hook can be used to force a VM to use a libvirt network that is managed
outside of ovirt, such as an openvswitch network, or libvirt's default network.
"""
from __future__ import absolute_import
from __future__ import print_function


import os
import sys
import traceback
import xml.dom

import hooking


def replaceSource(interface, newnet):
    source, = interface.getElementsByTagName('source')
    source.removeAttribute('bridge')
    source.setAttribute('network', newnet)
    interface.setAttribute('type', 'network')


def main():
    newnet = os.environ.get('extnet')
    if newnet is not None:
        doc = hooking.read_domxml()
        interface, = doc.getElementsByTagName('interface')
        replaceSource(interface, newnet)
        hooking.write_domxml(doc)


def test():

    interface = xml.dom.minidom.parseString("""
    <interface type="bridge">
        <address bus="0x00" domain="0x0000" function="0x0" slot="0x03"
                                            type="pci"/>
        <mac address="00:1a:4a:16:01:b0"/>
        <model type="virtio"/>
        <source bridge="ovirtmgmt"/>
        <filterref filter="vdsm-no-mac-spoofing"/>
        <link state="up"/>
        <boot order="1"/>
    </interface>
    """).getElementsByTagName('interface')[0]

    print("Interface before forcing network: %s" %
          interface.toxml(encoding='UTF-8'))

    replaceSource(interface, 'yipee')
    print("Interface after forcing network: %s" %
          interface.toxml(encoding='UTF-8'))


if __name__ == '__main__':
    try:
        if '--test' in sys.argv:
            test()
        else:
            main()
    except:
        hooking.exit_hook('extnet hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
