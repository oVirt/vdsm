#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

'''
Hook to bind a vNIC into a Bridge

Syntax:
   macbind={'vnicmac': 'bridgename'}

Example:
   macbind={'00:1a:4a:60:d1:9a': 'br0', '00:1a:4a:60:c4:88': 'br1'}
'''

from __future__ import absolute_import
from __future__ import print_function

import ast
import os
import sys
import traceback
import xml.dom
from vdsm.network.ipwrapper import Link

import hooking

_DEV_TYPE = frozenset(('bridge', 'openvswitch'))


def createElement(domxml, element, attribute, attributeValue):
    xmlElement = domxml.createElement(element)
    xmlElement.setAttribute(attribute, attributeValue)

    return xmlElement


def isDevice(dev):
    return os.path.exists('/sys/class/net/%s' % dev)


def replaceSourceBridge(domxml, macbindopts, devType=None):
    for vnicmac, vnicdev in macbindopts.iteritems():
        if devType is None:
            if isDevice(vnicdev):
                deviceType = Link._detectType(vnicdev)
            else:
                raise Exception("Invalid device [%s]" % vnicdev)
        else:
            deviceType = devType
        if deviceType in _DEV_TYPE:
            for iface in domxml.getElementsByTagName('interface'):
                mac = iface.getElementsByTagName('mac')[0]
                macaddr = mac.getAttribute('address')
                if macaddr == vnicmac:
                    oldsource = iface.getElementsByTagName('source')[0]
                    iface.removeChild(oldsource)
                    iface.appendChild(createElement(domxml,
                                                    'source',
                                                    'bridge',
                                                    vnicdev))
                    if deviceType == 'openvswitch':
                        iface.appendChild(createElement(domxml,
                                                        'virtualport',
                                                        'type',
                                                        'openvswitch'))
        else:
            raise Exception("Invalid device type [%s]" % deviceType)


def main():
    if 'macbind' in os.environ:
        macbindopts = ast.literal_eval(os.environ['macbind'])
        macbindopts = dict((k.lower(), v) for k, v in macbindopts.iteritems())
        domxml = hooking.read_domxml()
        replaceSourceBridge(domxml, macbindopts)
        hooking.write_domxml(domxml)


def test():
    text = '''<interface type="bridge">
<address bus="0x00" domain="0x0000" function="0x0" slot="0x03" type="pci"/>
<mac address="00:1a:4a:60:d1:9a"/>
<model type="virtio"/>
<filterref filter="vdsm-no-mac-spoofing"/>
<link state="up"/>
<source bridge="ovirtmgmt"/>
</interface>'''

    domxml = xml.dom.minidom.parseString(text)

    print("Interface before forcing device: %s" %
          domxml.toxml(encoding='UTF-8'))

    replaceSourceBridge(domxml, {'00:1a:4a:60:d1:9a': 'br0'}, "bridge")

    print("Interface after forcing device: %s" %
          domxml.toxml(encoding='UTF-8'))


if __name__ == '__main__':
    try:
        if '--test' in sys.argv:
            test()
        else:
            main()
    except:
        hooking.exit_hook(' macbind hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
