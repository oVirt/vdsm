#
# Copyright 2015 Red Hat, Inc.
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

from ..ipwrapper import DUMMY_BRIDGE
from .. import libvirtconnection


DUMMY_BRIDGE  # Appease flake8 since dummy bridge should be exported from here

NET_PATH = '/sys/class/net'
LIBVIRT_NET_PREFIX = 'vdsm-'


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
