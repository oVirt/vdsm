#
# Copyright 2015 Hat, Inc.
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
from __future__ import absolute_import
from datetime import datetime, timedelta
from glob import iglob
import six

# possible names of dhclient's lease files (e.g. as NetworkManager's slave)
DHCLIENT_LEASES_GLOBS = [
    '/var/lib/dhclient/dhclient*.lease*',  # iproute2 configurator, initscripts
    '/var/lib/NetworkManager/dhclient*-*.lease',
]


def get_dhclient_ifaces(lease_files_globs=DHCLIENT_LEASES_GLOBS):
    """Return a pair of sets containing ifaces configured using dhclient (-6)

    dhclient stores DHCP leases to file(s) whose names can be specified
    by the lease_files_globs parameter (an iterable of glob strings).
    """
    dhcpv4_ifaces, dhcpv6_ifaces = set(), set()

    for lease_files_glob in lease_files_globs:
        for lease_path in iglob(lease_files_glob):
            with open(lease_path) as lease_file:
                found_dhcpv4, found_dhcpv6 = _parse_lease_file(lease_file)
                dhcpv4_ifaces.update(found_dhcpv4)
                dhcpv6_ifaces.update(found_dhcpv6)

    return dhcpv4_ifaces, dhcpv6_ifaces


def _parse_lease_file(lease_file):
    IFACE = '  interface "'
    IFACE_END = '";\n'
    EXPIRE = '  expire '  # DHCPv4
    STARTS = '      starts '  # DHCPv6
    MAX_LIFE = '      max-life '
    VALUE_END = ';\n'

    family = None
    iface = None
    lease6_starts = None
    dhcpv4_ifaces, dhcpv6_ifaces = set(), set()

    for line in lease_file:
        if line == 'lease {\n':
            family = 4
            iface = None
            continue

        elif line == 'lease6 {\n':
            family = 6
            iface = None
            continue

        if family and line.startswith(IFACE) and line.endswith(IFACE_END):
            iface = line[len(IFACE):-len(IFACE_END)]

        elif family == 4:
            if line.startswith(EXPIRE):
                end = line.find(';')
                if end == -1:
                    continue  # the line should always contain a ;

                expiry_time = _parse_expiry_time(line[len(EXPIRE):end])
                if expiry_time is not None and datetime.utcnow() > expiry_time:
                    family = None
                    continue

            elif line == '}\n':
                family = None
                if iface:
                    dhcpv4_ifaces.add(iface)

        elif family == 6:
            if line.startswith(STARTS) and line.endswith(VALUE_END):
                timestamp = float(line[len(STARTS):-len(VALUE_END)])
                lease6_starts = datetime.utcfromtimestamp(timestamp)

            elif (lease6_starts and line.startswith(MAX_LIFE) and
                    line.endswith(VALUE_END)):
                seconds = float(line[len(MAX_LIFE):-len(VALUE_END)])
                max_life = timedelta(seconds=seconds)
                if datetime.utcnow() > lease6_starts + max_life:
                    family = None
                    continue

            elif line == '}\n':
                family = None
                if iface:
                    dhcpv6_ifaces.add(iface)

    return dhcpv4_ifaces, dhcpv6_ifaces


def propose_updates_to_reported_dhcp(network_info, networking):
    """
    Report DHCPv4/6 of a network's topmost device based on the network's
    configuration, to fix bug #1184497 (DHCP still being reported for hours
    after a network got static IP configuration, as reporting is based on
    dhclient leases).
    """
    updated_networking = dict(bondings={}, bridges={}, nics={}, vlans={})
    network_device = network_info['iface']

    for devices in ('bridges', 'vlans', 'bondings', 'nics'):
        dev_info = networking[devices].get(network_device)
        if dev_info:
            cfg = {}
            updated_networking[devices][network_device] = {
                'dhcpv4': network_info['dhcpv4'],
                'dhcpv6': network_info['dhcpv6'],
                'cfg': cfg,
            }
            cfg['BOOTPROTO'] = 'dhcp' if network_info['dhcpv4'] else 'none'
            break

    return updated_networking


def update_reported_dhcp(replacement, networking):
    """
    For each network device (representing a network), apply updates to reported
    DHCP-related fields, as prepared by _propose_updates_to_reported_dhcp.
    """
    for device_type, devices in six.iteritems(replacement):
        for device_name, replacement_device_info in six.iteritems(devices):
            device_info = networking[device_type][device_name]
            device_info['dhcpv4'] = replacement_device_info['dhcpv4']
            device_info['dhcpv6'] = replacement_device_info['dhcpv6']
            # Remove when cluster level < 3.6 is no longer supported and thus
            # it is not necessary to report ifcfg-like BOOTPROTO field.
            if replacement_device_info['cfg']:
                device_info['cfg'].update(replacement_device_info['cfg'])


def _parse_expiry_time(expiry_time):
    EPOCH = 'epoch '

    if expiry_time == 'never':
        return None
    elif expiry_time.startswith(EPOCH):
        since_epoch = expiry_time[len(EPOCH):]
        return datetime.utcfromtimestamp(float(since_epoch))

    else:
        return datetime.strptime(expiry_time, '%w %Y/%m/%d %H:%M:%S')


def dhcp_used(iface, ifaces_with_active_leases, net_attrs, family=4):
    if net_attrs is None:
        return iface in ifaces_with_active_leases
    else:
        try:
            if family == 4:
                return net_attrs['bootproto'] == 'dhcp'
            else:
                return net_attrs['dhcpv6']
        except KeyError:
            return False
