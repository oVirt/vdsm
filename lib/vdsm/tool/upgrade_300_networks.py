#
# Copyright 2011-2013 Red Hat, Inc.
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

import logging
import sys

from vdsm import netinfo
from vdsm.constants import MANAGEMENT_NETWORK
from vdsm.tool import expose
from vdsm.tool.upgrade import upgrade

sys.path.append("/usr/share/vdsm")
from netconf import ifcfg

UPGRADE_NAME = 'upgrade-3.0.0-networks'


def isNeeded(networks, bridges):
    return (MANAGEMENT_NETWORK not in networks and
            MANAGEMENT_NETWORK in bridges)


@upgrade(UPGRADE_NAME)
def run(networks, bridges):
    configWriter = ifcfg.ConfigWriter()

    # Create a network for every bridge that doesn't have one
    for bridge in bridges:
        if not bridge in networks:
            logging.debug('Creating network %s', bridge)
            configWriter.createLibvirtNetwork(network=bridge,
                                              bridged=True,
                                              skipBackup=True)

    # Remove all networks that don't have a bridge
    for network in networks:
        if networks[network]['bridged'] and network not in bridges:
            logging.debug('Removing network %s', network)
            configWriter.removeLibvirtNetwork(network, skipBackup=True)


@expose(UPGRADE_NAME)
def upgrade_networks(*args):
    """
    Since ovirt-3.0, Vdsm uses libvirt networks (with names vdsm-*) to store
    its own networks. Older Vdsms did not have those defined, and used only
    linux bridges. This command is kept as an upgrade tool for the (very few)
    people who still have such old setups running.
    """
    networks = netinfo.networks()
    bridges = netinfo.bridges()

    if isNeeded(networks, bridges):
        run(networks, bridges)
