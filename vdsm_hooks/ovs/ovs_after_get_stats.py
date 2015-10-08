#!/usr/bin/env python
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
from functools import partial
import traceback

import six

from vdsm.netconfpersistence import RunningConfig

import hooking

from ovs_utils import is_ovs_network
import ovs_utils

log = partial(ovs_utils.log, tag='ovs_after_get_stats: ')


def ovs_networks_stats(stats):
    """ Get OVS networks from RunningConfig and assign them network stats
    dictionaries from underlying devices. Fake bridges and bonds already
    have stats with their names.
    """
    ovs_networks_stats = {}
    running_config = RunningConfig()

    for network, attrs in six.iteritems(running_config.networks):
        if is_ovs_network(attrs):
            vlan = attrs.get('vlan')
            iface = attrs.get('nic') or attrs.get('bonding')
            if vlan is None:
                # Untagged networks use OVS bridge as their bridge, but Engine
                # expects a bridge with 'network-name' name.  create a copy of
                # top underlying device stats and save it as bridge's stats.
                # NOTE: copy stats of ovsbr0? (now we repots iface's stats)
                ovs_networks_stats[network] = stats[iface]
            else:
                # Engine expects stats entries for vlans named 'iface.id'
                vlan_name = '%s.%s' % (iface, vlan)
                ovs_networks_stats[vlan_name] = stats[network]
                ovs_networks_stats[vlan_name]['name'] = vlan_name

    log('Updating network stats with OVS networks: %s' % ovs_networks_stats)
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
