#!/usr/bin/python2
# Copyright 2015 Red Hat, Inc.
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
from functools import partial
import errno
import os
import traceback

import hooking

from ovs_utils import INIT_CONFIG_FILE
import ovs_utils

log = partial(ovs_utils.log, tag='ovs_after_network_setup: ')


def _remove_init_config():
    try:
        os.remove(INIT_CONFIG_FILE)
    except OSError as e:
        # we do not save INIT_CONFIG_FILE until we change system configuration
        if e.errno != errno.ENOENT:
            raise


def main():
    setup_nets_config = hooking.read_json()

    in_ovs_rollback = setup_nets_config['request']['options'].get(
        '_inOVSRollback')

    if in_ovs_rollback:
        log('Rollback is done. Removing OVS init_config backup.')
    else:
        log('Network setup was successful. Removing OVS init_config backup.')

    _remove_init_config()


if __name__ == '__main__':
    try:
        main()
    except:
        hooking.exit_hook(traceback.format_exc())
