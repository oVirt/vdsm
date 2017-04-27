# Copyright 2016-2017 Red Hat, Inc.
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
import logging
import logging.config
import threading

from vdsm.network import netrestore
from vdsm.network import netupgrade
from vdsm.network.restore_net_config import restore

from . import expose, ExtraArgsError


@expose('restore-nets-init')
def retore_nets_init(*args):
    """
    restore-nets-init

    Restore IP+link configuration on persisted OVS networks.
    """
    netrestore.init_nets()


@expose('upgrade-networks')
def upgrade_networks(*args):
    """
    upgrade-networks

    Upgrade networks configuration to up-to-date format.
    """
    netupgrade.upgrade()


@expose('restore-nets')
def restore_command(*args):
    """
    restore-nets
    Restores the networks to what was previously persisted via vdsm.
    """
    threading.current_thread().setName('restore-net')
    try:
        logging.config.fileConfig('/etc/vdsm/svdsm.logger.conf',
                                  disable_existing_loggers=False)
    except:
        logging.basicConfig(filename='/dev/stderr', filemode='w+',
                            level=logging.DEBUG)
        logging.error('Could not init proper logging', exc_info=True)

    if len(args) > 2:
        raise ExtraArgsError()

    force_restore = '--force' in args
    restore(force_restore)
