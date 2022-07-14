# Copyright 2016-2022 Red Hat, Inc.
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


class OptStringParser:
    def __init__(self, delim=' ', assign_op='='):
        self._delim = delim
        self._assign_op = assign_op

    def parse(self, opts_str: str):
        if opts_str:
            opts = opts_str.strip(self._delim).split(self._delim)
            return dict(opt.split(self._assign_op, 1) for opt in opts)
        return {}


class NmstateBridgeOpts(OptStringParser):
    def __init__(self):
        super(NmstateBridgeOpts, self).__init__()
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

    def parse(self, opts: str):
        """
        param: opts: vdsm.network.nmstate.bridge_util.NetworkConfig bridge opts
        """
        if not opts:
            return {}
        bridge_opts = super().parse(opts.replace('_', '-'))
        try:
            for key, value in bridge_opts.items():
                bridge_opts[key] = self._convert_value(key, value)
        except KeyError:
            raise NmstateBridgeOptionNotSupported(
                f'{key} is not a supported bridge option'
            )
        return bridge_opts

    def _convert_value(self, key, value):
        conversion_func = self._types_dict[key]
        return conversion_func(value)

    @staticmethod
    def _str_to_bool(str_value):
        return str_value.lower() not in ['false', '0']


class NmstateBridgeOptionNotSupported(Exception):
    pass
