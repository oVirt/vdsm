#!/usr/bin/env python
# Copyright 2016 Red Hat, Inc.
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
from __future__ import print_function

import sys
import xml.etree.cElementTree as ET

import six

# TODO: Remove the direct access to VDSM internal and interact with hooking api
from vdsm import netconfpersistence

# The caller of this hook is libvirt, therefore there is a need to specify
# the path to VDSMDIR before importing ovs_utils (or more precisely hooking)
_P_VDSM = '/usr/share/vdsm/'
sys.path += [_P_VDSM]
import ovs_utils


_DEBUG_MODE = False

if _DEBUG_MODE:
    LOG_FILE = '/tmp/libvirthook_ovs_migrate.log'
    log = open(LOG_FILE, 'w')


def main(domain, event, phase, *args, **kwargs):
    if event not in ('migrate', 'restore'):
        sys.exit(0)

    if _DEBUG_MODE:
        print('Hook input args are: ', domain, event, phase, file=log)

    stdin = kwargs.get('stdin', sys.stdin)

    tree = ET.parse(stdin)
    _process_domxml(tree)

    stdout = kwargs.get('stdout', sys.stdout)
    tree.write(stdout)

    if _DEBUG_MODE:
        tree.write(log)
        print('\nEnd of hook', file=log)


def _process_domxml(tree):
    root = tree.getroot()
    devices = root.find('devices')
    running_config = netconfpersistence.RunningConfig()
    for interface in devices.findall('interface'):
        if interface.get('type') == 'bridge':

            elem_virtualport = interface.find('virtualport')
            elem_source = interface.find('source')

            # 'source bridge' element must exist
            source_bridge = elem_source.get('bridge')
            source_bridge = _find_non_vlan_network(running_config,
                                                   source_bridge)

            net_attrs = running_config.networks.get(source_bridge)
            if net_attrs is None:
                print('Network', source_bridge, 'does not exist',
                      file=sys.stderr)
                sys.exit(1)

            if ovs_utils.is_ovs_network(net_attrs):
                _bind_iface_to_ovs(elem_source, elem_virtualport, interface,
                                   net_attrs)
            else:
                _bind_iface_to_linux_bridge(elem_source, elem_virtualport,
                                            interface, net_attrs,
                                            source_bridge)


def _bind_iface_to_linux_bridge(elem_source, elem_virtualport, interface,
                                net_attrs, source_bridge):
    if elem_virtualport is not None:
        _convert_source_to_linux_bridge(elem_source, elem_virtualport,
                                        interface, net_attrs, source_bridge)


def _convert_source_to_linux_bridge(elem_source, elem_virtualport, interface,
                                    net_attrs, source_bridge):
    interface.remove(elem_virtualport)
    if (net_attrs.get('vlan') is None and
            elem_source.get('bridge') == ovs_utils.BRIDGE_NAME):
        elem_source.set('bridge', source_bridge)


def _bind_iface_to_ovs(elem_source, elem_virtualport, interface, net_attrs):
    if elem_virtualport is None:
        _convert_source_to_ovs(elem_source, interface, net_attrs)


def _convert_source_to_ovs(elem_source, interface, net_attrs):
    elem_virtualport = ET.SubElement(interface, 'virtualport')
    elem_virtualport.set('type', 'openvswitch')
    elem_virtualport.tail = ' '
    if net_attrs.get('vlan') is None:
        elem_source.set('bridge', ovs_utils.BRIDGE_NAME)


def _find_non_vlan_network(running_config, source_bridge):
    # If the source bridge is the OVS switch name (its default bridge)
    # it cannot be validated against the running config, as it is not a
    # net name. Instead, a non vlan network is looked for and if found,
    # it is assumed to be the target network (raising the limitation of
    # having a single non-vlan network on a host).
    if source_bridge == ovs_utils.BRIDGE_NAME:
        nets = [net for net, attrs in six.iteritems(
            running_config.networks) if attrs.get('vlan') is None]
        if len(nets) == 1:
            source_bridge = nets[0]
        else:
            print('Detected', len(nets), 'non-vlan networks:', nets,
                  '\n',
                  'This hook supports only a single non-vlan net.',
                  file=sys.stderr)
            sys.exit(1)
    return source_bridge


if __name__ == '__main__':
    main(*sys.argv[1:])
