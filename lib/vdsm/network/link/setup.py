# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
