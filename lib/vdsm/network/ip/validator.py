# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import six

from vdsm.network import errors as ne

from .address import IPAddressData, IPAddressDataError


def validate(nets):
    for net, attrs in six.iteritems(nets):
        if 'remove' in attrs:
            continue

        validate_static_ipv4_config(attrs)
        _validate_nameservers(net, attrs)


def _validate_nameservers(net, attrs):
    if attrs['nameservers']:
        _validate_nameservers_network(attrs)
        _validate_nameservers_address(attrs['nameservers'])


def _validate_nameservers_network(attrs):
    if not attrs['defaultRoute']:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'Name servers may only be defined on the default host network',
        )


def _validate_nameservers_address(nameservers_addr):
    for addr in nameservers_addr:
        addr = _normalize_address(addr)
        try:
            IPAddressData(addr, device=None)
        except IPAddressDataError as e:
            raise ne.ConfigNetworkError(ne.ERR_BAD_ADDR, str(e))


def validate_static_ipv4_config(net_attrs):
    if 'ipaddr' in net_attrs:
        try:
            address = '{}/{}'.format(
                net_attrs['ipaddr'], net_attrs.get('netmask', '')
            )
            IPAddressData(address, device=None)
            if 'gateway' in net_attrs:
                IPAddressData(net_attrs['gateway'], device=None)
        except IPAddressDataError as e:
            raise ne.ConfigNetworkError(ne.ERR_BAD_ADDR, str(e))
        if net_attrs.get('bootproto') == 'dhcp':
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_ADDR,
                'mixing static ip configuration with ' 'dhcp is not allowed',
            )
    else:
        if 'gateway' in net_attrs or 'netmask' in net_attrs:
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_ADDR,
                'gateway or netmask were given ' 'without ip address',
            )


def _normalize_address(addr):
    """
    The nameserver address may be tailed with the interface from which it
    should be reached: 'fe80::1%eth0'
    Please see zone identifier RFC for more information:
        https://tools.ietf.org/html/rfc6874
    For the purpose of address validation, such tail is ignored.
    """
    return addr.split('%', 1)[0]
