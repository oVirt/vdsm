#!/usr/bin/python

import os
import sys
import hooking
import traceback

filtername = 'isolatedprivatevlan-vdsm'


def addInterfaceFilter(domxml, interface, gateway, ip):
    if not ip is None:
        filterref = domxml.createElement('filterref')
        filterref.setAttribute('filter', 'clean-traffic')
        interface.appendChild(filterref)

        parameter = domxml.createElement('parameter')
        parameter.setAttribute('name', 'IP')
        parameter.setAttribute('value', ip)
        filterref.appendChild(parameter)

    filterref = domxml.createElement('filterref')
    filterref.setAttribute('filter', filtername)
    interface.appendChild(filterref)

    parameter = domxml.createElement('parameter')
    parameter.setAttribute('name', 'GATEWAY_MAC')
    parameter.setAttribute('value', gateway)
    filterref.appendChild(parameter)

if 'isolatedprivatevlan' in os.environ:
    try:
        try:
            gateway, ip = os.environ['isolatedprivatevlan'].split(',')
        except ValueError:
            gateway = os.environ['isolatedprivatevlan']
            ip = None

        domxml = hooking.read_domxml()
        domain = domxml.getElementsByTagName('domain')[0]
        interfaces = domxml.getElementsByTagName('interface')

        for interface in interfaces:
            addInterfaceFilter(domxml, interface, gateway, ip)

        hooking.write_domxml(domxml)

    except:
        sys.stderr.write('isolated-privatevlan: [unexpected error]: %s\n' %
                         traceback.format_exc())
        sys.exit(2)
