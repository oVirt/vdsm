# Copyright 2011-2013 Red Hat, Inc.
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
from xml.sax.saxutils import escape
import libvirt

from vdsm import libvirtconnection
from vdsm import netinfo


def getNetworkDef(network):
    netName = netinfo.LIBVIRT_NET_PREFIX + network
    conn = libvirtconnection.get()
    try:
        net = conn.networkLookupByName(netName)
        return net.XMLDesc(0)
    except libvirt.libvirtError as e:
        if e.get_error_code() == libvirt.VIR_ERR_NO_NETWORK:
            return

        raise


def createNetworkDef(network, bridged=True, iface=None):
    netName = netinfo.LIBVIRT_NET_PREFIX + network
    if bridged:
        return '''<network><name>%s</name><forward mode='bridge'/>
                    <bridge name='%s'/></network>''' % (escape(netName),
                                                        escape(network))
    else:
        return (
            '''<network><name>%s</name><forward mode='passthrough'>'''
            '''<interface dev='%s'/></forward></network>''' %
            (escape(netName), escape(iface)))


def createNetwork(netXml):
    conn = libvirtconnection.get()
    net = conn.networkDefineXML(netXml)
    net.create()
    net.setAutostart(1)


def removeNetwork(network):
    netName = netinfo.LIBVIRT_NET_PREFIX + network
    conn = libvirtconnection.get()

    net = conn.networkLookupByName(netName)
    if net.isActive():
        net.destroy()
    if net.isPersistent():
        net.undefine()
