#!/usr/bin/python
"""
Removes spoofing filtering, applied on a per vnic basis,
it gets triggered and used by two different events:
    * before_device_create
    * before_nic_hotplug
"""


import os
import hooking


def removeMacSpoofingFilter(interface):
    for filterElement in interface.getElementsByTagName('filterref'):
        if isMacSpoofingFilter(filterElement):
            interface.removeChild(filterElement)


def isMacSpoofingFilter(filterElement):
    """
    Accept a filter DOM element
    and checks if it's a mac spoofing filter
    """
    filterValue = filterElement.getAttribute('filter')
    return filterValue == 'vdsm-no-mac-spoofing'


def main():

    if hooking.tobool(os.environ.get('ifacemacspoof')):
        domxml = hooking.read_domxml()
        interface, = domxml.getElementsByTagName('interface')
        removeMacSpoofingFilter(interface)
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

    print "Interface before removing filter: %s" % \
        interface.toxml(encoding='UTF-8')

    removeMacSpoofingFilter(interface)
    print "Interface after removing filter: %s" % \
        interface.toxml(encoding='UTF-8')


if __name__ == '__main__':
    main()
