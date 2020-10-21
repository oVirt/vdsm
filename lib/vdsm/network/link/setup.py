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

from vdsm.network.link.bridge import Bridge


def parse_bond_options(options):
    """
    Parse bonding options into a dictionary.
    """

    def _string_to_dict(str, div, eq):
        if options == '':
            return {}
        return dict(
            option.split(eq, 1) for option in str.strip(div).split(div)
        )

    if options:
        d_options = _string_to_dict(options, ' ', '=')
        return d_options
    else:
        return {}


def setup_custom_bridge_opts(nets):
    for name, opts in parse_nets_bridge_opts(nets):
        Bridge(name, opts)


def parse_nets_bridge_opts(nets):
    for name, opts in nets.items():
        opts_str = opts.get('custom', {}).get('bridge_opts')
        if opts_str:
            bridge_opts = dict(
                opt.split('=', 1) for opt in opts_str.split(' ')
            )
            yield name, bridge_opts
