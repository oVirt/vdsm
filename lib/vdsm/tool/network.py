# Copyright 2016 Red Hat, Inc.
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

from vdsm.network import netrestore
from vdsm.network import netupgrade

from . import expose


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
