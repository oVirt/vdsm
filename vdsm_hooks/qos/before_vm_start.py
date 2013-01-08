#!/usr/bin/python

import os
import sys
import ast
import hooking
import traceback

'''
QoS Hook
========
libvirt domain xml:
<interface>
    ...
    <bandwidth>
        <inbound average='1' peak='2' burst='5'/>
        <outbound average='0.5'/>
    </bandwidth>
    ...
</interface>

Note:
    for average, peak, and burst explanation look at:
    # man tc tbf

'average' attribute is mandatory,
inbound or outbound elements can be once but not mandatory

syntax:
    MAC1=in{'average':'1','peek':'2','burst':'5'}^out{'average':'1'}&MAC2=...
where:
    MACn should be replaced with the MAC addresses of the virtual nics to be
    edited.'''

keys = ['average', 'peek', 'burst']


def add_attributes(node, attributes):
    data = ast.literal_eval(attributes)
    for key in data.keys():
        if not key in keys:
            sys.stderr.write('qos hook: bad attribute name %s\n' % key)
            sys.exit(2)

        node.setAttribute(key, data[key])


def update_interface(iface, data, domxml):
    bandwidth = domxml.createElement('bandwidth')
    iface.appendChild(bandwidth)

    for i in data.split('^'):
        if i[:2] == 'in':
            inbound = domxml.createElement('inbound')
            add_attributes(inbound, i[2:])
            bandwidth.appendChild(inbound)
        elif i[:3] == 'out':
            outbound = domxml.createElement('outbound')
            add_attributes(outbound, i[3:])
            bandwidth.appendChild(outbound)
        else:
            sys.stderr.write('qos hook: bad input %s\n' % i)
            sys.exit(2)


def find_interface(mac, interfaces):
    for iface in interfaces:
        mac_elem = iface.getElementsByTagName('mac')[0]
        if mac_elem.getAttribute('address').lower() == mac.lower():
            return iface
    return None

if 'qos' in os.environ:
    try:
        domxml = hooking.read_domxml()
        interfaces = domxml.getElementsByTagName('interface')

        for entry in os.environ['qos'].split('&'):
            arr = entry.split('=')

            iface = find_interface(arr[0], interfaces)
            if iface is None:
                sys.stderr.write('qos hook: %s interface is not exists in '
                                 'VM\n' % arr[0])
                sys.exit(2)

            update_interface(iface, arr[1], domxml)

        hooking.write_domxml(domxml)
    except:
        sys.stderr.write('qos hook: [unexpected error]: %s\n' %
                         traceback.format_exc())
        sys.exit(2)
