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

from vdsm.netinfo.cache import (libvirtNets2vdsm, get as netinfo_get,
                                CachingNetInfo)
from vdsm.netinfo import networks as libvirt_nets

from . import connectivity
from . import legacy_switch


def validate(networks, bondings):
    legacy_switch.validate_network_setup(networks, bondings)


def setup(networks, bondings, options, in_rollback):
    _setup_legacy(networks, bondings, options, in_rollback)


def _setup_legacy(networks, bondings, options, in_rollback):

    _libvirt_nets = libvirt_nets()
    _netinfo = CachingNetInfo(netinfo_get(libvirtNets2vdsm(_libvirt_nets)))

    with legacy_switch.ConfiguratorClass(in_rollback) as configurator:
        # from this point forward, any exception thrown will be handled by
        # Configurator.__exit__.

        legacy_switch.remove_networks(networks, bondings, configurator,
                                      _netinfo, _libvirt_nets)

        legacy_switch.bonds_setup(bondings, configurator, _netinfo,
                                  in_rollback)

        legacy_switch.add_missing_networks(configurator, networks,
                                           bondings, _netinfo)

        connectivity.check(options)
