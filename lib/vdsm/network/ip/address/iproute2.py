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

from __future__ import absolute_import
from __future__ import division

import contextlib
import sys

import six

from vdsm.network import ipwrapper
from vdsm.network.netlink import addr as nl_addr

from . import IPAddressAddError, IPAddressDeleteError
from . import IPAddressData, IPAddressApi


class IPAddress(IPAddressApi):
    @staticmethod
    def add(addr_data):
        with _translate_iproute2_exception(IPAddressAddError, addr_data):
            ipwrapper.addrAdd(
                addr_data.device,
                addr_data.address,
                addr_data.prefixlen,
                addr_data.family,
            )

    @staticmethod
    def delete(addr_data):
        with _translate_iproute2_exception(IPAddressDeleteError, addr_data):
            ipwrapper.addrDel(
                addr_data.device,
                addr_data.address,
                addr_data.prefixlen,
                addr_data.family,
            )

    @staticmethod
    def addresses(device=None, family=None):
        addrs = nl_addr.iter_addrs()
        filtered = IPAddress._filter_addresses(addrs, device, family)
        for address in filtered:
            yield IPAddressData(
                address=address['address'],
                device=address['label'],
                flags=address['flags'],
                scope=address['scope'],
            )

    @staticmethod
    def _filter_addresses(addrs, device_name, family_number):
        family_name = (
            IPAddress._address_family_name(family_number)
            if family_number is not None
            else None
        )
        for address in addrs:
            if (device_name is None or address['label'] == device_name) and (
                family_name is None or address['family'] == family_name
            ):
                yield address

    @staticmethod
    def _address_family_name(family_number):
        if family_number == 4:
            return 'inet'
        elif family_number == 6:
            return 'inet6'
        else:
            raise AttributeError(
                'Unknown IP family number [{}].'.format(family_number)
            )


@contextlib.contextmanager
def _translate_iproute2_exception(new_exception, address_data):
    try:
        yield
    except ipwrapper.IPRoute2Error:
        _, value, tb = sys.exc_info()
        error_message = value.args[1][0]
        six.reraise(
            new_exception, new_exception(str(address_data), error_message), tb
        )
