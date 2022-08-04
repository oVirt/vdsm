# Copyright 2022 Red Hat, Inc.
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

import os

from vdsm.network.nmstate.schema import LinuxBridge


class OptStringParser:
    def __init__(self, delim=' ', assign_op='='):
        self._delim = delim
        self._assign_op = assign_op

    def parse(self, opts_str: str):
        if opts_str:
            opts = opts_str.strip(self._delim).split(self._delim)
            return dict(opt.split(self._assign_op, 1) for opt in opts)
        return {}


class BridgeOptsSchema:
    _TICKS_PER_SEC = os.sysconf('SC_CLK_TCK')

    def __init__(self):
        self._schema = {
            LinuxBridge.Options.MAC_AGEING_TIME: int,
            LinuxBridge.Options.GROUP_FORWARD_MASK: int,
            LinuxBridge.Options.HASH_MAX: int,
            LinuxBridge.Options.MULTICAST_SNOOPING: self.booleanize,
            LinuxBridge.Options.MULTICAST_ROUTER: int,
            LinuxBridge.Options.MULTICAST_LAST_MEMBER_COUNT: int,
            LinuxBridge.Options.MULTICAST_LAST_MEMBER_INTERVAL: int,
            LinuxBridge.Options.MULTICAST_MEMBERSHIP_INTERVAL: int,
            LinuxBridge.Options.MULTICAST_QUERIER: self.booleanize,
            LinuxBridge.Options.MULTICAST_QUERIER_INTERVAL: int,
            LinuxBridge.Options.MULTICAST_QUERY_USE_IFADDR: self.booleanize,
            LinuxBridge.Options.MULTICAST_QUERY_INTERVAL: int,
            LinuxBridge.Options.MULTICAST_QUERY_RESPONSE_INTERVAL: int,
            LinuxBridge.Options.MULTICAST_STARTUP_QUERY_COUNT: int,
            LinuxBridge.Options.MULTICAST_STARTUP_QUERY_INTERVAL: int,
            LinuxBridge.STP_SUBTREE: {
                LinuxBridge.STP.ENABLED: self.booleanize,
                LinuxBridge.STP.FORWARD_DELAY: self.os_ticks_to_secs,
                LinuxBridge.STP.HELLO_TIME: self.os_ticks_to_secs,
                LinuxBridge.STP.MAX_AGE: self.os_ticks_to_secs,
                LinuxBridge.STP.PRIORITY: int,
            },
        }

    @classmethod
    def os_ticks_to_secs(cls, str_value):
        return int(int(str_value) / cls._TICKS_PER_SEC)

    @classmethod
    def booleanize(cls, str_value):
        return str_value.lower() not in ['false', '0']

    @property
    def schema(self):
        return self._schema


class BridgeOptsBuilder(OptStringParser):
    def __init__(self):
        super(BridgeOptsBuilder, self).__init__()
        self._opts_schema = BridgeOptsSchema()
        self._opts_dict = {}

    def parse(self, opts: str, stp=False) -> {}:
        """
        param: opts: vdsm.network.nmstate.bridge_util.NetworkConfig bridge opts
        """
        self._opts_dict = super().parse(opts.replace('_', '-')) if opts else {}
        result_opts = self._build(self._opts_schema.schema, result={})
        if self._opts_dict:
            raise BridgeOptionNotSupported(
                f'{[k for k in self._opts_dict.keys()]}'
                ' not supported as bridge option(s)'
            )
        result_opts[LinuxBridge.STP_SUBTREE][LinuxBridge.STP.ENABLED] = stp
        return result_opts

    def _build(self, opts_schema, result):
        for k, conversion_func in opts_schema.items():
            if type(conversion_func) is dict:
                result[k] = {}
                self._build(opts_schema[k], result[k])
            else:
                try:
                    result[k] = conversion_func(self._opts_dict[k])
                    del self._opts_dict[k]
                except KeyError:
                    pass
        return result


class BridgeOptionNotSupported(Exception):
    pass
