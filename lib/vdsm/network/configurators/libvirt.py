# Copyright 2011-2015 Red Hat, Inc.
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
from __future__ import absolute_import

import xml.etree.cElementTree as etree
from xml.sax.saxutils import escape

from libvirt import libvirtError, VIR_ERR_NO_NETWORK

from vdsm import libvirtconnection
from vdsm.network import netinfo


def getNetworkDef(network):
    netName = netinfo.LIBVIRT_NET_PREFIX + network
    conn = libvirtconnection.get()
    try:
        net = conn.networkLookupByName(netName)
        return net.XMLDesc(0)
    except libvirtError as e:
        if e.get_error_code() == VIR_ERR_NO_NETWORK:
            return

        raise


def createNetworkDef(network, bridged=True, iface=None):
    """
    Creates Network Xml e.g.:
    <network>
        <name>vdsm-awesome_net</name>

        <forward mode='bridge'/><bridge name='awesome_net'/> ||
        <forward mode='passthrough'><interface dev='incredible'/></forward>
    </network>

    Forward mode can be either bridge or passthrough,
    according to net if bridged or bridgeless this
    determines respectively the presence of bridge element
    or interface subelement.
    """

    netName = netinfo.LIBVIRT_NET_PREFIX + network

    def EtreeElement(tagName, text=None, **attrs):
        elem = etree.Element(tagName)
        if text:
            elem.text = escape(text)
        if attrs:
            for attr, value in attrs.iteritems():
                elem.set(attr, escape(str(value)))
        return elem

    root = etree.Element('network')
    nameElem = EtreeElement('name', netName)
    forwardElem = EtreeElement(
        'forward', mode='bridge' if bridged else 'passthrough'
    )
    root.append(nameElem)
    root.append(forwardElem)
    if bridged:
        root.append(EtreeElement('bridge', name=network))
    else:
        forwardElem.append(EtreeElement('interface', dev=iface))
    return etree.tostring(root)


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
