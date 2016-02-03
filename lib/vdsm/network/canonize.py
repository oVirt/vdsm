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

import six

from vdsm.netinfo import (bridges, mtus)
from vdsm import utils

from .errors import ConfigNetworkError
from . import errors as ne


def canonize_networks(nets):
    """
    Given networks configuration, explicitly add missing defaults.
    :param nets: The network configuration
    """
    for attrs in six.itervalues(nets):
        # If net is marked for removal, normalize the mark to boolean and
        # ignore all other attributes canonization.
        if _canonize_remove(attrs):
                continue

        _canonize_mtu(attrs)
        _canonize_vlan(attrs)
        _canonize_bridged(attrs)
        _canonize_stp(attrs)


def _canonize_remove(data):
    if 'remove' in data:
        data['remove'] = utils.tobool(data['remove'])
        return data['remove']
    return False


def _canonize_mtu(data):
    data['mtu'] = int(data['mtu']) if 'mtu' in data else mtus.DEFAULT_MTU


def _canonize_vlan(data):
    vlan = data.get('vlan', None)
    if vlan in (None, ''):
        data.pop('vlan', None)
    else:
        data['vlan'] = int(vlan)


def _canonize_bridged(data):
    if 'bridged' in data:
        data['bridged'] = utils.tobool(data['bridged'])
    else:
        data['bridged'] = True


def _canonize_stp(data):
    if data['bridged']:
        stp = False
        if 'stp' in data:
            stp = data['stp']
        elif 'STP' in data:
            stp = data.pop('STP')
        try:
            data['stp'] = bridges.stp_booleanize(stp)
        except ValueError:
            raise ConfigNetworkError(ne.ERR_BAD_PARAMS, '"%s" is not '
                                     'a valid bridge STP value.' % stp)
