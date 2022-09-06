# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from collections import defaultdict
import logging
import threading

import six
import xml.etree.ElementTree as etree
from xml.sax.saxutils import escape

from libvirt import libvirtError, VIR_ERR_NO_NETWORK

from vdsm.common import libvirtconnection
from vdsm.common import xmlutils


LIBVIRT_NET_PREFIX = 'vdsm-'

_libvirt_net_lock = threading.Lock()


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
    return xmlutils.tostring(root)


def create_network(netname, iface, user_reference=None):
    """
    Create a libvirt network if it does not yet exist.
    The user_reference argument is a unique identifier of the caller,
    used to track the network users.
    """
    with _libvirt_net_lock:
        if not is_libvirt_network(netname):
            bridged = netname == iface
            iface = None if bridged else iface
            _createNetwork(createNetworkDef(netname, bridged, iface))

        NetworksUsersCache.add(netname, user_reference)


def delete_network(netname, user_reference=None):
    """
    Remove a libvirt network when all its users have asked to remove it.
    """
    with _libvirt_net_lock:
        NetworksUsersCache.remove(netname, user_reference)
        if not NetworksUsersCache.has_users(netname):
            removeNetwork(netname)


def _createNetwork(netXml):
    conn = libvirtconnection.get()
    net = conn.networkDefineXML(netXml)
    net.create()
    net.setAutostart(1)


def removeNetwork(network):
    netName = LIBVIRT_NET_PREFIX + network
    conn = libvirtconnection.get()

    net = _netlookup_by_name(conn, netName)
    if net:
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
            xml = etree.fromstring(net.XMLDesc())
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


def netname_o2l(ovirt_name):
    """Translate ovirt network name to the name used by libvirt database"""
    return LIBVIRT_NET_PREFIX + ovirt_name


def netname_l2o(libvirt_name):
    """Translate the name used by libvirt database to the ovirt network name"""
    return libvirt_name[len(LIBVIRT_NET_PREFIX):]


def _netlookup_by_name(conn, netname):
    try:
        return conn.networkLookupByName(netname)
    except libvirtError as e:
        if e.get_error_code() == VIR_ERR_NO_NETWORK:
            return None
        raise


class NetworksUsersCache(object):
    """
    Manages networks users reference.
    Note: The implementation is NOT thread safe.
    """
    _nets_users = defaultdict(set)

    @staticmethod
    def add(net, user_ref):
        if (net in NetworksUsersCache._nets_users and
                user_ref in NetworksUsersCache._nets_users[net]):
            logging.warning('Attempting to add an existing net user: %s/%s',
                            net, user_ref)

        NetworksUsersCache._nets_users[net].add(user_ref)

    @staticmethod
    def remove(net, user_ref):
        if net not in NetworksUsersCache._nets_users:
            logging.warning('Attempting to remove a non existing network: '
                            '%s/%s', net, user_ref)

        net_users = NetworksUsersCache._nets_users[net]
        try:
            net_users.remove(user_ref)
        except KeyError:
            logging.warning('Attempting to remove a non existing net user: '
                            '%s/%s', net, user_ref)
        if len(net_users) == 0:
            del NetworksUsersCache._nets_users[net]

    @staticmethod
    def has_users(net):
        if net not in NetworksUsersCache._nets_users:
            return False
        return len(NetworksUsersCache._nets_users[net]) > 0
