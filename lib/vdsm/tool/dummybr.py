# Copyright 2012-2017 Red Hat, Inc.
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
from __future__ import division

import os

from libvirt import libvirtError, VIR_ERR_NO_NETWORK

from vdsm.common import commands
from vdsm.common import libvirtconnection
from vdsm.network.api import DUMMY_BRIDGE
from . import expose, ExtraArgsError

EXT_IP = '/usr/sbin/ip'


def createEphemeralBridge(bridgeName):
    commands.run([
        EXT_IP, 'link', 'add', bridgeName, 'type', 'bridge'])


def removeEphemeralBridge(bridgeName):
    commands.run([
        EXT_IP, 'link', 'del', bridgeName, 'type', 'bridge'])


def addBridgeToLibvirt(bridgeName):
    conn = libvirtconnection.get(None, False)
    if bridgeName not in conn.listNetworks():
        conn.networkCreateXML(
            '''<network><name>%s</name><forward mode='bridge'/><bridge '''
            '''name='%s'/></network>''' % (bridgeName, bridgeName))


def removeBridgeFromLibvirt(bridgeName):
    dummy_network = _getLibvirtNetworkByName(bridgeName)
    if dummy_network and dummy_network.isActive():
        dummy_network.destroy()


def _getLibvirtNetworkByName(networkName):
    conn = libvirtconnection.get(killOnFailure=False)
    try:
        return conn.networkLookupByName(networkName)
    except libvirtError as e:
        if e.get_error_code() == VIR_ERR_NO_NETWORK:
            return None
        raise


@expose('dummybr')
def create_dummybr_deprecated(*args):
    """
    dummybr

    Defines dummy bridge on libvirt network.

    Deprecated in favor of dummybr-create.
    """
    create_dummybr(*args)


@expose('dummybr-create')
def create_dummybr(*args):
    """
    dummybr-create

    Defines dummy bridge on libvirt network.
    """

    if len(args) > 1:
        raise ExtraArgsError()

    if not os.path.exists('/sys/class/net/%s' % DUMMY_BRIDGE):
        createEphemeralBridge(DUMMY_BRIDGE)
    addBridgeToLibvirt(DUMMY_BRIDGE)


@expose('dummybr-remove')
def remove_dummybr(*args):
    """
    dummybr-remove

    Undefines dummy bridge on libvirt network.
    """

    if len(args) > 1:
        raise ExtraArgsError()

    if os.path.exists('/sys/class/net/%s' % DUMMY_BRIDGE):
        removeEphemeralBridge(DUMMY_BRIDGE)
    removeBridgeFromLibvirt(DUMMY_BRIDGE)
