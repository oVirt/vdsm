# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager
import logging

from vdsm.common.config import config
from vdsm.network import bond_monitor
from vdsm.network import dhcp_monitor
from vdsm.network import ipwrapper
from vdsm.network import lldp

Lldp = lldp.driver()


def init_privileged_network_components():
    _lldp_init()


def init_unprivileged_network_components(cif, net_api):
    dhcp_monitor.initialize_monitor(cif, net_api)
    bond_monitor.initialize_monitor(cif)


def stop_unprivileged_network_components():
    dhcp_monitor.Monitor.instance().stop()
    bond_monitor.stop()


@contextmanager
def init_unpriviliged_dhcp_monitor_ctx(event_sink, net_api):
    with dhcp_monitor.initialize_monitor_ctx(event_sink, net_api):
        yield


def _lldp_init():
    """
    Enables receiving of LLDP frames for all nics. If sending or receiving
    LLDP frames is already enabled on a nic, it is not modified.
    """
    if not config.getboolean('vars', 'enable_lldp'):
        logging.warning('LLDP is disabled')
        return

    if Lldp.is_active():
        for device in ipwrapper.nic_links():
            if not Lldp.is_lldp_enabled_on_iface(device.name):
                try:
                    Lldp.enable_lldp_on_iface(device.name)
                except lldp.EnableLldpError:
                    logging.warning(
                        'Ignoring failure to enable LLDP on %s',
                        device.name,
                        exc_info=True,
                    )
    else:
        logging.warning('LLDP is inactive, skipping LLDP initialization')
