# Copyright 2016-2020 Red Hat, Inc.
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


class NmstateBridgeOpts:
    def __init__(self):
        self._types_dict = {
            'mac-ageing-time': int,
            'group-forward-mask': int,
            'hash-max': int,
            'multicast-snooping': self._str_to_bool,
            'multicast-router': int,
            'multicast-last-member-count': int,
            'multicast-last-member-interval': int,
            'multicast-membership-interval': int,
            'multicast-querier': self._str_to_bool,
            'multicast-querier-interval': int,
            'multicast-query-use-ifaddr': self._str_to_bool,
            'multicast-query-interval': int,
            'multicast-query-response-interval': int,
            'multicast-startup-query-count': int,
            'multicast-startup-query-interval': int,
        }

    @property
    def types_dict(self):
        return self._types_dict

    def convert_value(self, key, value):
        conversion_func = self._types_dict[key]
        return conversion_func(value)

    @staticmethod
    def _str_to_bool(str_value):
        str_value = str_value.lower()
        false_values = ['false', '0']
        return str_value not in false_values


class NmstateBridgeOptionNotSupported(Exception):
    pass


def parse_bond_options(options):
    """
    Parse bonding options into a dictionary.
    """
    if options:
        d_options = _string_to_dict(options, ' ', '=')
        return d_options
    else:
        return {}


def parse_nets_bridge_opts(opts):

    if not opts:
        return {}
    opts_str = opts.replace('_', '-')
    bridge_opts = _string_to_dict(opts_str, ' ', '=')
    try:
        nmstate_bridge_opts = NmstateBridgeOpts()
        for key, value in bridge_opts.items():
            bridge_opts[key] = nmstate_bridge_opts.convert_value(key, value)
    except KeyError:
        raise NmstateBridgeOptionNotSupported(
            f'{key} is not a valid nmstate bridge option'
        )
    return bridge_opts


def _string_to_dict(str, div, eq):
    if str == '':
        return {}
    return dict(option.split(eq, 1) for option in str.strip(div).split(div))
