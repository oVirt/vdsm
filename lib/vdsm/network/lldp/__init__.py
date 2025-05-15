# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import abc

from vdsm.network import driverloader


class EnableLldpError(Exception):
    pass


class DisableLldpError(Exception):
    pass


class TlvReportLldpError(Exception):
    pass


class LldpAPI(object):
    """LLDP driver interface"""

    __metaclass__ = abc.ABCMeta

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
