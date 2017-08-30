# Copyright 2017 Red Hat, Inc.
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

from vdsm import supervdsm
from vdsm.network import dhclient_monitor
from vdsm.network import lldp
from vdsm.network.ipwrapper import getLinks
from vdsm.network.nm import networkmanager

Lldp = lldp.driver()


def init_privileged_network_components():
    networkmanager.init()
    _lldp_init()


def init_unprivileged_network_components(cif):
    _init_sourceroute()
    _register_notifications(cif)
    dhclient_monitor.start()


def _lldp_init():
    """"
    Enables receiving of LLDP frames for all nics. If sending or receiving
    LLDP frames is already enabled on a nic, it is not modified.
    """
    if Lldp.is_active():
        for device in (link for link in getLinks() if link.isNIC()):
            if not Lldp.is_lldp_enabled_on_iface(device.name):
                try:
                    Lldp.enable_lldp_on_iface(device.name)
                except lldp.EnableLldpError:
                    logging.warning('Ignoring failure to enable LLDP on %s',
                                    device.name, exc_info=True)
    else:
        logging.warning('LLDP is inactive, skipping LLDP initialization')


def _init_sourceroute():
    def _add_sourceroute(iface, ip, mask, route):
        supervdsm.getProxy().add_sourceroute(iface, ip, mask, route)

    def _remove_sourceroute(iface):
        supervdsm.getProxy().remove_sourceroute(iface)

    dhclient_monitor.register_action_handler(
        action_type=dhclient_monitor.ActionType.CONFIGURE,
        action_function=_add_sourceroute,
        required_fields=(dhclient_monitor.ResponseField.IFACE,
                         dhclient_monitor.ResponseField.IPADDR,
                         dhclient_monitor.ResponseField.IPMASK,
                         dhclient_monitor.ResponseField.IPROUTE))
    dhclient_monitor.register_action_handler(
        action_type=dhclient_monitor.ActionType.REMOVE,
        action_function=_remove_sourceroute,
        required_fields=(dhclient_monitor.ResponseField.IFACE,))


def _register_notifications(cif):
    def _notify(**kwargs):
        cif.notify('|net|host_conn|no_id')

    dhclient_monitor.register_action_handler(
        action_type=dhclient_monitor.ActionType.CONFIGURE,
        action_function=_notify,
        required_fields=(dhclient_monitor.ResponseField.IFACE,
                         dhclient_monitor.ResponseField.IPADDR,
                         dhclient_monitor.ResponseField.IPMASK))
