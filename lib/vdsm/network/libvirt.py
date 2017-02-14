# Copyright 2011-2016 Red Hat, Inc.
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

from glob import iglob
import logging
import six
import xml.etree.cElementTree as etree
from xml.sax.saxutils import escape

from libvirt import libvirtError, VIR_ERR_NO_NETWORK

from vdsm import libvirtconnection

LIBVIRT_NET_PREFIX = 'vdsm-'


def getNetworkDef(network):
    netName = LIBVIRT_NET_PREFIX + network
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

    netName = LIBVIRT_NET_PREFIX + network

    def EtreeElement(tagName, text=None, **attrs):
        elem = etree.Element(tagName)
        if text:
            elem.text = escape(text)
        if attrs:
            for attr, value in six.iteritems(attrs):
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
    netName = LIBVIRT_NET_PREFIX + network
    conn = libvirtconnection.get()

    net = conn.networkLookupByName(netName)
    if net.isActive():
        net.destroy()
    if net.isPersistent():
        net.undefine()


def networks():
    """
    Get dict of networks from libvirt

    :returns: dict of networkname={properties}
    :rtype: dict of dict
            { 'ovirtmgmt': { 'bridge': 'ovirtmgmt', 'bridged': True}
              'red': { 'iface': 'red', 'bridged': False}}
    """
    nets = {}
    conn = libvirtconnection.get()
    allNets = ((net, net.name()) for net in conn.listAllNetworks(0))
    for net, netname in allNets:
        if netname.startswith(LIBVIRT_NET_PREFIX):
            netname = netname[len(LIBVIRT_NET_PREFIX):]
            nets[netname] = {}
            xml = etree.fromstring(net.XMLDesc(0))
            interface = xml.find('.//interface')
            if interface is not None:
                nets[netname]['iface'] = interface.get('dev')
                nets[netname]['bridged'] = False
            else:
                nets[netname]['bridge'] = xml.find('.//bridge').get('name')
                nets[netname]['bridged'] = True
    return nets


def is_libvirt_network(netname):
    conn = libvirtconnection.get()
    libvirt_nets = conn.listAllNetworks()
    netname = LIBVIRT_NET_PREFIX + netname
    return any(n.name() == netname for n in libvirt_nets)


def is_libvirt_device(device):
    try:
        libvirt_nets = networks()
    except libvirtError:
        logging.error('Libvirt failed to answer. It might be the case that'
                      ' this call is being run before libvirt startup. '
                      ' Thus, check if vdsm owns %s an alternative way' %
                      device)
        return _is_device_configured_in_a_network(device)
    libvirt_devices = [net.get('bridge') or net.get('iface')
                       for net in six.itervalues(libvirt_nets)]
    return device in libvirt_devices


def _is_device_configured_in_a_network(device):
    """
    Checks whether the device belongs to libvirt when libvirt is not yet
    running (network.service runs before libvirtd is started). To do so,
    it must check if there is an autostart network that uses the device.
    """
    bridged_name = "bridge name='%s'" % device
    bridgeless_name = "interface dev='%s'" % device
    for filename in iglob('/etc/libvirt/qemu/networks/autostart/vdsm-*'):
        with open(filename) as xml_file:
            xml_content = xml_file.read()
            if bridged_name in xml_content or bridgeless_name in xml_content:
                return True
    return False
