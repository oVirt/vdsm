#!/usr/bin/python2
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
from libvirt import libvirtError
import six

from vdsm.compat import suppress
from vdsm.network import libvirt


def prepare_libvirt(nets, running_config):
    libvirt_create = {}
    libvirt_remove = set()

    for net, attrs in six.iteritems(nets):
        if 'remove' in attrs:
            libvirt_remove.add(net)
        else:
            if net in running_config.networks:
                libvirt_remove.add(net)
            libvirt_network_xml = libvirt.createNetworkDef(
                net, bridged=True, iface=(attrs.get('nic') or
                                          attrs.get('bonding')))
            libvirt_create[net] = libvirt_network_xml

    return libvirt_create, libvirt_remove


def create_libvirt_nets(libvirt_create):
    for net_xml in six.itervalues(libvirt_create):
        libvirt.createNetwork(net_xml)


def remove_libvirt_nets(libvirt_remove):
    for net in libvirt_remove:
        with suppress(libvirtError):
            libvirt.removeNetwork(net)
