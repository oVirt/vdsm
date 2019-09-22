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
from __future__ import division

from contextlib import contextmanager
import logging
import os

import six

from vdsm.common.constants import P_VDSM_RUN
from vdsm.common.config import config
from vdsm.common.conv import tobool
from vdsm.network import netswitch
from vdsm.network.link import setup
from vdsm.network.link.bond import Bond

from .netconfpersistence import PersistentConfig


NETS_RESTORED_MARK = os.path.join(P_VDSM_RUN, 'nets_restored')


def init_nets():
    persistence = config.get('vars', 'net_persistence')
    if persistence != 'unified':
        logging.info('Skipping: Unified persistence is not used.')
        return

    if _nets_already_restored(NETS_RESTORED_MARK):
        logging.info('Skipping: Networks were already restored.')
        return

    logging.info('Starting initial network setup.')

    persistent_config = PersistentConfig()

    nets = _persisted_ovs_entries(persistent_config.networks)
    logging.info('Restoring networks configuration: {}'.format(nets))
    _set_blocking_dhcp(nets)

    bonds = _persisted_ovs_entries(persistent_config.bonds)
    logging.info('Restoring bondings configuration: {}'.format(bonds))

    for net, attrs in six.iteritems(nets):
        with _try2execute('IPv6autoconf for {} failed.'.format(net)):
            netswitch.configurator.setup_ipv6autoconf({net: attrs})

    for bond_name, attrs in six.iteritems(bonds):
        with _try2execute('Restoration of bond {} failed.'.format(bond_name)):
            requested_slaves = set(attrs['nics'])
            requested_options = (
                setup.parse_bond_options(attrs['options'])
                if 'options' in attrs
                else None
            )
            with Bond(
                bond_name, slaves=requested_slaves, options=requested_options
            ) as bond:
                bond.create()

    for bond, attrs in six.iteritems(bonds):
        with _try2execute('Setting links up for {} failed.'.format(bond)):
            netswitch.configurator.set_ovs_links_up({}, {bond: attrs}, {})

    for net, attrs in six.iteritems(nets):
        with _try2execute('Setting links up for {} failed.'.format(net)):
            netswitch.configurator.set_ovs_links_up({net: attrs}, {}, {})

    for net, attrs in six.iteritems(nets):
        with _try2execute('IP config for {} failed.'.format(net)):
            netswitch.configurator.setup_ovs_ip_config({net: attrs}, {})

    logging.info('Initial network setup is done.')


def _persisted_ovs_entries(persistent_entries):
    return {
        entry: attrs
        for entry, attrs in six.iteritems(persistent_entries)
        if attrs.get('switch') == 'ovs'
    }


def _nets_already_restored(nets_restored_mark):
    return os.path.exists(nets_restored_mark)


def _set_blocking_dhcp(networks):
    for attrs in six.itervalues(networks):
        if attrs.get('bootproto') == 'dhcp' or tobool(attrs.get('dhcpv6')):
            attrs['blockingdhcp'] = True


@contextmanager
def _try2execute(log):
    try:
        yield
    except Exception:
        logging.exception(log)
