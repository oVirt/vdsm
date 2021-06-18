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
from __future__ import division

import abc

import six

from vdsm.network import driverloader


class EnableLldpError(Exception):
    pass


class DisableLldpError(Exception):
    pass


class TlvReportLldpError(Exception):
    pass


@six.add_metaclass(abc.ABCMeta)
class LldpAPI(object):
    """LLDP driver interface"""

    @staticmethod
    def enable_lldp_on_iface(iface, rx_only=True):
        raise NotImplementedError

    @staticmethod
    def disable_lldp_on_iface(iface):
        raise NotImplementedError

    @staticmethod
    def is_lldp_enabled_on_iface(iface):
        raise NotImplementedError

    @staticmethod
    def get_tlvs(iface):
        """
        Report all tlv identifiers.
        :return: TLV reports in a dict format where the TLV ID/s are the keys.
        """
        raise NotImplementedError

    @staticmethod
    def is_active(iface):
        raise NotImplementedError


class Drivers(object):
    LLDPAD = 'lldpad'


def driver(driver_name=Drivers.LLDPAD):
    _drivers = driverloader.load_drivers('Lldp', __name__, __path__[0])
    return driverloader.get_driver(driver_name, _drivers)
