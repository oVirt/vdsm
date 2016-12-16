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
import traceback

import six

from vdsm.network.netconfpersistence import RunningConfig

import hooking

from ovs_utils import is_ovs_network


def ovs_networks_stats(stats):
    """Get OVS networks from RunningConfig and assign them network stats
    dictionaries from underlying devices. Fake bridges and bonds already have
    stats with their names.

    Note, that it takes some time for a new device to appear in stats, so we
    first check if the device we got from running_config is already reported.
    """
    ovs_networks_stats = {}
    running_config = RunningConfig()

    for network, attrs in six.iteritems(running_config.networks):
        if is_ovs_network(attrs):
            vlan = attrs.get('vlan')
            iface = attrs.get('nic') or attrs.get('bonding')
            if vlan is None and iface in stats:
                # Untagged networks use OVS bridge as their bridge, but Engine
                # expects a bridge with 'network-name' name.  create a copy of
                # top underlying device stats and save it as bridge's stats.
                # NOTE: copy stats of ovsbr0? (now we repots iface's stats)
                ovs_networks_stats[network] = stats[iface]
                ovs_networks_stats[network]['name'] = network
            elif network in stats:
                # Engine expects stats entries for vlans named 'iface.id'
                vlan_name = '%s.%s' % (iface, vlan)
                ovs_networks_stats[vlan_name] = stats[network]
                ovs_networks_stats[vlan_name]['name'] = vlan_name

    return ovs_networks_stats


def main():
    stats = hooking.read_json()
    stats['network'].update(ovs_networks_stats(stats['network']))
    hooking.write_json(stats)


if __name__ == '__main__':
    try:
        main()
    except:
        hooking.exit_hook(traceback.format_exc())
