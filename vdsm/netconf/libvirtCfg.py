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
import libvirt
from xml.dom.minidom import Document
from xml.sax.saxutils import escape

from vdsm import libvirtconnection
from vdsm import netinfo


def flush():
    conn = libvirtconnection.get()
    allNets = ((net, net.name()) for net in conn.listAllNetworks(0))
    for net, netname in allNets:
        if netname.startswith(netinfo.LIBVIRT_NET_PREFIX):
            if net.isActive():
                net.destroy()
            if net.isPersistent():
                net.undefine()


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


def createNetworkDef(network, bridged=True, iface=None,
                     qosInbound=None, qosOutbound=None):
    """
    Creates Network Xml e.g.:
    <network>
        <name>vdsm-awesome_net</name>

        <forward mode='bridge'/><bridge name='awesome_net'/> ||
        <forward mode='passthrough'><interface dev='incredible'/></forward>

        [<bandwidth>]
            [<inbound average='1000' [peak='5000'] [burst='1024']/>]
            [<outbound average='1000' [burst='1024']/>]
        [</bandwidth>]
    </network>

    Forward mode can be either bridge or passthrough,
    according to net if bridged or bridgeless this
    determines respectively the presence of bridge element
    or interface subelement. Inbound or outbound element
    can be optionally defined.
    """

    netName = netinfo.LIBVIRT_NET_PREFIX + network

    def XmlElement(tagName, text=None, **attrs):
        elem = Document().createElement(tagName)
        if text:
            textNode = Document().createTextNode(escape(text))
            elem.appendChild(textNode)
        if attrs:
            for attr, value in attrs.iteritems():
                elem.setAttribute(attr, escape(str(value)))
        return elem

    root = XmlElement('network')
    nameElem = XmlElement('name', netName)
    forwardElem = XmlElement('forward',
                             mode='bridge' if bridged else 'passthrough')
    root.appendChild(nameElem)
    root.appendChild(forwardElem)
    if bridged:
        root.appendChild(XmlElement('bridge', name=network))
    else:
        forwardElem.appendChild(XmlElement('interface', dev=iface))

    if qosInbound or qosOutbound:
        bandwidthElem = XmlElement('bandwidth')
        if qosInbound:
            bandwidthElem.appendChild(XmlElement('inbound', **qosInbound))
        if qosOutbound:
            bandwidthElem.appendChild(XmlElement('outbound',
                                                 **qosOutbound))
        root.appendChild(bandwidthElem)

    return root.toxml()


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
