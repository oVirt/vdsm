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

from contextlib import contextmanager

import six

from vdsm.netconfpersistence import RunningConfig
from vdsm.netinfo.cache import CachingNetInfo

from . import validator

SWITCH_TYPE = 'ovs'


def validate_network_setup(nets, bonds):
    running_networks = RunningConfig().networks
    kernel_nics = CachingNetInfo().nics

    for net, attrs in six.iteritems(nets):
        validator.validate_net_configuration(net, attrs, running_networks)
    for bond, attrs in six.iteritems(bonds):
        validator.validate_bond_configuration(attrs, kernel_nics)


@contextmanager
def rollback_trigger(in_rollback):
    try:
        yield
    except:
        pass
    finally:
        pass


def setup(nets, bonds):
    pass
