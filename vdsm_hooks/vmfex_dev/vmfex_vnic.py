#!/usr/bin/python
#
# Copyright 2013 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
"""
Modify a network interface definition so that it uses a Cisco VM-FEX Port
Profile with a Virtual Function from a pool. It gets triggered and used by two
different events:
    * before_device_create
    * before_nic_hotplug
"""
from __future__ import print_function
from xml.dom import minidom
import fcntl
import os
import sys
import traceback

from vdsm import libvirtconnection
import hooking

VMFEX_NET_POOL = 'direct-pool'


def getUsableNics():
    """Scans the system for physical nics that have zeroes as MAC address,
    as they will be the free VFs that can be used with the hook."""
    nics = []
    for path, dirnames, filenames in os.walk('/sys/devices/', topdown=True):
        if path == '/sys/devices/':
            for badDir in [d for d in dirnames if not d.startswith('pci')]:
                dirnames.remove(badDir)
        if 'address' in filenames:
            with open(os.path.join(path, 'address')) as addrFile:
                mac = addrFile.read().strip()
            if mac == '00:00:00:00:00:00':
                nics.append(os.path.basename(path))
    return nics


def createDirectPool(conn):
    """Creates a libvirt network holding the pool of Virtual Functions that
    are available on the host."""
    if 'direct-pool' in conn.listNetworks():
        directPool = conn.networkLookupByName('direct-pool')
        # destroy and undefine direct-pool
        directPool.destroy()
        directPool.undefine()
        sys.stderr.write('vmfex_dev: removed direct-pool \n')

    # create a new direct-pool
    content = (['<network>', '<name>direct-pool</name>',
               '<forward mode="passthrough">'] +
               ['<interface dev="%s"/>' % nic for nic in getUsableNics()] +
               ['</forward>', '</network>'])
    xmlDefinition = '\n'.join(content)
    conn.networkDefineXML(xmlDefinition)
    directPool = conn.networkLookupByName('direct-pool')
    directPool.setAutostart(1)
    directPool.create()
    sys.stderr.write('vmfex_dev: created Direct-Pool Net \n')
    sys.stderr.write(xmlDefinition + '\n')


def qbhInUse(conn):
    """Returns whether there is already some VM with a 802.1Qbh (most likely to
    be vmfex)."""
    for vmid in conn.listDomainsID():
        # FIXME: we have to hold a reference to domobj due to rhbz#1305338
        domobj = conn.lookupByID(vmid)
        domxml = minidom.parseString(domobj.XMLDesc(0))
        for vport in domxml.getElementsByTagName('virtualport'):
            if vport.getAttribute('type') == '802.1Qbh':
                return True
    return False


def isDirectPoolUpToDate(conn):
    """Returns whether the currently defined direct pool has exactly the same
    devices as are available in the system."""
    directPool = conn.networkLookupByName('direct-pool')
    directPoolXml = minidom.parseString(directPool.XMLDesc(0))
    currentPoolNics = set([dev.getAttribute('dev') for dev in
                           directPoolXml.getElementsByTagName('interface')])
    return currentPoolNics == set(getUsableNics())


def handleDirectPool(conn):
    """Takes care that a libvirt network that holds the VFs in a pool to allow
    migration of VMs that use VM-FEX exists or is created."""
    with open('/var/run/vdsm/hook-vmfex.lock', 'w') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            if 'direct-pool' not in conn.listNetworks():
                createDirectPool(conn)

            elif not qbhInUse(conn) and not isDirectPoolUpToDate(conn):
                createDirectPool(conn)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def attachProfileToInterfaceXml(interface, portProfile):
    """Defines a VM-FEX virtual port (SR-IOV passthrough with Macvtap) to be
    attached to the provided interface."""
    source, = interface.getElementsByTagName('source')
    source.removeAttribute('bridge')
    source.setAttribute('network', VMFEX_NET_POOL)
    virtualPort = interface.ownerDocument.createElement('virtualport')
    virtualPort.setAttribute('type', '802.1Qbh')
    interface.appendChild(virtualPort)
    parameters = interface.ownerDocument.createElement('parameters')
    parameters.setAttribute('profileid', portProfile)
    virtualPort.appendChild(parameters)
    interface.setAttribute('type', 'network')


def removeFilter(interface):
    for filterElement in interface.getElementsByTagName('filterref'):
        interface.removeChild(filterElement)


def test():
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

    print("Interface before attaching to VM-FEX: %s" %
          interface.toprettyxml(encoding='UTF-8'))

    attachProfileToInterfaceXml(interface, 'Profail')
    removeFilter(interface)

    print("Interface after attaching to VM-FEX port: %s" %
          interface.toprettyxml(encoding='UTF-8'))

    print('Available interfaces for the VM-FEX direct pool in the current '
          'host: %s' % getUsableNics())


def _migration_script():
    """Return true if this script runs as a migration destination script"""
    dirname = os.path.split(
        os.path.dirname(os.path.abspath(__file__)))[1]
    return dirname == 'before_device_migrate_destination'


def main():
    portProfile = os.environ.get('vmfex')
    if portProfile is not None:
        handleDirectPool(libvirtconnection.get())
        if not _migration_script():
            doc = hooking.read_domxml()
            interface, = doc.getElementsByTagName('interface')
            attachProfileToInterfaceXml(interface, portProfile)
            removeFilter(interface)
            hooking.write_domxml(doc)


if __name__ == '__main__':
    try:
        if '--test' in sys.argv:
            test()
        else:
            main()
    except:
        hooking.exit_hook('vmfex_dev hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
