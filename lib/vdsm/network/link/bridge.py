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

import glob
import os

import vdsm.network.errors as ne

IFACE_PATH = '/sys/class/net/{}'
BRIDGING_OPTS = IFACE_PATH + '/bridge/{}'
SKIPPED_BRIDGE_OPTIONS = ('flush',)


class Bridge(object):
    def __init__(self, name, options=None):
        self.name = name
        self.options = {}
        if options:
            self.set_options(options)
        else:
            self.read_options()

    def set_options(self, options):
        self.options = options
        self._persist_bridge_opts()
        self.read_options()

    def read_options(self):
        self.options = self._get_sysfs_bridge_options()

    def _persist_bridge_opts(self):
        if self.options:
            for opt, val in self.options.items():
                self._try_writing_single_opt(opt, val)

    def _try_writing_single_opt(self, opt, val):
        try:
            with open(BRIDGING_OPTS.format(self.name, opt), 'w') as f:
                f.write(val)
        except OSError:
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_PARAMS,
                f'Trying to write custom bridge option {opt}={val}'
                f' that does not exists for bridge {self.name}',
            )

    def _get_sysfs_bridge_options(self):
        """Returns a dictionary of bridge option name and value. E.g.,
        {'max_age': '2000', 'gc_timer': '332'}"""
        paths = glob.iglob(BRIDGING_OPTS.format(self.name, '*'))
        opts = {}

        for path in paths:
            key = os.path.basename(path)
            if key in SKIPPED_BRIDGE_OPTIONS:
                continue
            with open(path) as optFile:
                opts[key] = optFile.read().strip()

        return opts
