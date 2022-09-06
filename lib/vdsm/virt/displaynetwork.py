# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.network import api as net_api
from vdsm.virt import libvirtnetwork


def create_network(netname, user_reference=None):
    display_device = _display_device(netname)
    libvirtnetwork.create_network(netname, display_device, user_reference)


def delete_network(netname, user_reference=None):
    libvirtnetwork.delete_network(netname, user_reference)


def _display_device(netname):
    return net_api.network_northbound(netname)
