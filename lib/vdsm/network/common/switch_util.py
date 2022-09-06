# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later


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
