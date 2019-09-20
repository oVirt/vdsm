#
# Copyright 2016-2018 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import errno
import os

from vdsm.network import cmd
from vdsm.network import sourceroute
from vdsm.network.link.bond import sysfs_options_mapper

from .compat import mock
from .nettestlib import has_sysfs_bond_permission


TESTS_STATIC_PATH = os.path.join(os.path.dirname(__file__), 'static')

ALTERNATIVE_BONDING_DEFAULTS = os.path.join(
    TESTS_STATIC_PATH, 'bonding-defaults.json'
)

ALTERNATIVE_BONDING_NAME2NUMERIC_PATH = os.path.join(
    TESTS_STATIC_PATH, 'bonding-name2numeric.json'
)

bonding_dump_patchers = []


def setup_package():
    bonding_dump_patchers.append(
        mock.patch(
            'vdsm.network.link.bond.sysfs_options.BONDING_DEFAULTS',
            ALTERNATIVE_BONDING_DEFAULTS,
        )
    )
    bonding_dump_patchers.append(
        mock.patch(
            'vdsm.network.link.bond.sysfs_options_mapper.'
            'BONDING_NAME2NUMERIC_PATH',
            ALTERNATIVE_BONDING_NAME2NUMERIC_PATH,
        )
    )

    for patcher in bonding_dump_patchers:
        patcher.start()

    if has_sysfs_bond_permission():
        try:
            sysfs_options_mapper.dump_bonding_options()
        except EnvironmentError as e:
            if e.errno != errno.ENOENT:
                raise

    if os.geteuid() == 0:
        _pre_cleanup_stale_iprules()


def teardown_package():
    for patcher in bonding_dump_patchers:
        patcher.stop()


def _pre_cleanup_stale_iprules():
    commands = [
        'bash',
        '-c',
        'while ip rule delete prio {} 2>/dev/null; do true; done'.format(
            sourceroute.RULE_PRIORITY
        ),
    ]
    cmd.exec_sync(commands)
