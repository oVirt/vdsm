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
