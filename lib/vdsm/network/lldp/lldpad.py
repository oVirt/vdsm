# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.network.lldpad import lldptool

from . import LldpAPI


class Lldp(LldpAPI):
    @staticmethod
    def enable_lldp_on_iface(iface, rx_only=True):
        lldptool.enable_lldp_on_iface(iface, rx_only)

    @staticmethod
    def disable_lldp_on_iface(iface):
        lldptool.disable_lldp_on_iface(iface)

    @staticmethod
    def is_lldp_enabled_on_iface(iface):
        return lldptool.is_lldp_enabled_on_iface(iface)

    @staticmethod
    def get_tlvs(iface):
        return lldptool.get_tlvs(iface)

    @staticmethod
    def is_active():
        return (
            lldptool.is_lldpad_service_running()
            and lldptool.is_lldptool_functional()
        )
