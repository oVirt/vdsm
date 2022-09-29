# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import ipaddress
import logging
import socket
import sys

from vdsm.common import cache
from vdsm.network import ipwrapper


class Flags(object):
    PERMANENT = 'permanent'
    SECONDARY = 'secondary'


class IPAddressData(object):
    def __init__(self, address, device, scope=None, flags=None):
        try:
            self._address = ipaddress.ip_interface(str(address))
        except ValueError:
            _, val, tb = sys.exc_info()
            raise IPAddressDataError(val).with_traceback(tb)
        self._device = device
        self._flags = flags
        self._scope = scope

    @property
    def device(self):
        return self._device

    @property
    def family(self):
        return self._address.version

    @property
    def address(self):
        return str(self._address.ip)

    @property
    def netmask(self):
        return str(self._address.netmask)

    @property
    def prefixlen(self):
        return self._address.network.prefixlen

    @property
    def address_with_prefixlen(self):
        return str(self._address)

    @property
    def scope(self):
        return self._scope

    @property
    def flags(self):
        return self._flags

    def is_primary(self):
        return Flags.SECONDARY not in self.flags

    def is_permanent(self):
        return Flags.PERMANENT in self.flags

    def __repr__(self):
        rep = 'device={!r} address={!r} scope={!r} flags={!r}'.format(
            self._device, self._address, self._scope, self._flags
        )
        return 'IPAddressData({})'.format(rep)


class IPAddressError(Exception):
    pass


class IPAddressDataError(IPAddressError):
    pass


def set_default_route(gateway, family, dev=None):
    try:
        ipwrapper.routeAdd(['default', 'via', gateway], family=family, dev=dev)
    except ipwrapper.IPRoute2Error:  # there already is a default route
        logging.warning(
            'Existing default route will be removed so a new one can be set.'
        )
        ipwrapper.routeDel(['default'], family=family)
        ipwrapper.routeAdd(['default', 'via', gateway], family=family, dev=dev)


@cache.memoized
def ipv6_supported():
    """
    Check if IPv6 is disabled by kernel arguments (or even compiled out).
    """
    try:
        socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    except OSError:
        return False
    return True


def prefix2netmask(prefix):
    try:
        iface = ipaddress.ip_interface(f'0.0.0.0/{prefix}')
        return str(iface.network.netmask)
    except ValueError:
        raise ValueError(
            f'{prefix} is not a valid prefix value. It must be between '
            '0 and 32'
        )
