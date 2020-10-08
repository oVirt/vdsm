# Copyright 2020 Red Hat, Inc.
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


class SwitchType(object):
    LINUX_BRIDGE = 'legacy'
    OVS = 'ovs'


def split_switch_type(desired_config, running_config):
    ovs = {}
    linux_bridge = {}
    for name, attrs in desired_config.items():
        if _to_remove(attrs):
            # Removal of non-existant network will default to legacy switch
            r_attrs = running_config.get(
                name, {'switch': SwitchType.LINUX_BRIDGE}
            )
            switch = _get_switch_type(r_attrs)
        else:
            switch = _get_switch_type(attrs)

        if switch == SwitchType.LINUX_BRIDGE:
            linux_bridge[name] = attrs
        elif switch == SwitchType.OVS:
            ovs[name] = attrs

    return ovs, linux_bridge


def _to_remove(attrs):
    return attrs.get('remove', False)


def _get_switch_type(attrs):
    return attrs.get('switch')
