#!/usr/bin/python2
"""
This hook allows to replace the default no-mac-spoof filter with a the stricter
clean-traffic. To activate this hook
- install this file as a before_vm_start hook
- define the noipspoof custom property
sudo engine-config -s "UserDefinedVMProperties=noipspoof=^[0-9.]*$"
- enabled per a specfic VM by setting its noipspoof to a comman-separated list
  of valid IP addresses for it.
"""

import os
import sys

import hooking


def replaceMacSpoofingFilter(interface, IPs):
    for filterElement in interface.getElementsByTagName('filterref'):
        if isMacSpoofingFilter(filterElement):
            replaceFilter(interface, filterElement, IPs)


def isMacSpoofingFilter(filterElement):
    """
    Accept a filter DOM element
    and checks if it's a mac spoofing filter
    """
    filterValue = filterElement.getAttribute('filter')
    return filterValue == 'vdsm-no-mac-spoofing'


def replaceFilter(interface, filterElement, IPs):
    """
    Accept an interface DOM element
    and a filter DOM element and remove the filter from the DOM.
    """
    filterElement.attributes['filter'] = 'clean-traffic'
    for ip in IPs:
        param = filterElement.ownerDocument.createElement('parameter')
        param.attributes['name'] = 'IP'
        param.attributes['value'] = ip
        filterElement.appendChild(param)


def main():

    IPs = os.environ.get('noipspoof', '').split(',')
    if IPs:
        domxml = hooking.read_domxml()
        interface, = domxml.getElementsByTagName('interface')
        replaceMacSpoofingFilter(interface, IPs)
        hooking.write_domxml(domxml)


def test():
    import xml.dom

    interface = xml.dom.minidom.parseString("""
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

    print "Original interface: %s\n" % \
        interface.toxml(encoding='UTF-8')

    replaceMacSpoofingFilter(interface, ['192.168.1.1', 'nonsense'])
    print "Interface after replacing filter: %s\n" % \
        interface.toxml(encoding='UTF-8')


if __name__ == '__main__':
    if '--test' in sys.argv:
        test()
    else:
        main()
