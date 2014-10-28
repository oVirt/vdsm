#!/usr/bin/env python
# Copyright 2014 Red Hat, Inc.
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
import hooking
import traceback


def main():
    """Forward IPv6 configuration from the network 'custom' properties
       to VDSM API."""
    setup_nets_config = hooking.read_json()
    for network, attrs in setup_nets_config['request']['networks'].items():
        if 'remove' in attrs:
            continue
        elif 'custom' in attrs:
            _process_network(attrs)
    hooking.write_json(setup_nets_config)


def _process_network(attrs):
    for property_name in ('ipv6addr', 'ipv6gateway', 'ipv6autoconf', 'dhcpv6'):
        value = attrs['custom'].get(property_name)
        if value is not None:
            attrs[property_name] = value


if __name__ == '__main__':
    try:
        main()
    except:
        hooking.exit_hook('ipv6 hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
