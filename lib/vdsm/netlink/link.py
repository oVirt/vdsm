# Copyright 2014 Red Hat, Inc.
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
from ctypes import (CFUNCTYPE, byref, c_char, c_char_p, c_int, c_void_p,
                    c_size_t, sizeof)
from functools import partial
import errno

from . import _cache_manager, _nl_cache_get_first, _nl_cache_get_next
from . import _char_proto, _int_char_proto, _int_proto, _void_proto
from . import (_ethtool_uses_libnl3, LIBNL_ROUTE, _nl_addr2str, _nl_geterror,
               _pool)

CHARBUFFSIZE = 40  # Increased to fit IPv6 expanded representations
HWADDRSIZE = 60    # InfiniBand HW address needs 59+1 bytes


def get_link(name):
    """Returns the information dictionary of the name specified link."""
    with _pool.socket() as sock:
        with _nl_link_cache(sock) as cache:
            link = _rtnl_link_get_by_name(cache, name)
            if not link:
                raise IOError(errno.ENODEV, '%s is not present in the system' %
                              name)
            return _link_info(cache, link)


def iter_links():
    """Generator that yields an information dictionary for each link of the
    system."""
    with _pool.socket() as sock:
        with _nl_link_cache(sock) as cache:
            link = _nl_cache_get_first(cache)
            while link:
                yield _link_info(cache, link)
                link = _nl_cache_get_next(link)


def _link_info(cache, link):
    """Returns a dictionary with the information of the link object."""
    info = {}
    info['address'] = _link_address(link)
    info['flags'] = _rtnl_link_get_flags(link)
    info['index'] = _rtnl_link_get_ifindex(link)
    info['mtu'] = _rtnl_link_get_mtu(link)
    info['name'] = _rtnl_link_get_name(link)
    info['qdisc'] = _rtnl_link_get_qdisc(link)
    info['state'] = _link_state(link)

    # libnl-1 has a bug when getting type information.
    # https://github.com/tgraf/libnl-1.1-stable/issues/1
    # TODO: Add for libnl1 if the bug is fixed
    if _ethtool_uses_libnl3():
        link_type = _rtnl_link_get_type(link)
        if link_type is not None:
            info['type'] = link_type

    underlying_device_index = _rtnl_link_get_link(link)
    if underlying_device_index > 0:
        info['device'] = _link_index_to_name(cache, underlying_device_index)

    master_index = _rtnl_link_get_master(link)
    if master_index > 0:
        info['master'] = _link_index_to_name(cache, master_index)

    vlanid = _rtnl_link_vlan_get_id(link)
    if vlanid >= 0:
        info['vlanid'] = vlanid

    return info


def _link_index_to_name(cache, link_index):
    """Returns the textual name of the link with index equal to link_index."""
    name = (c_char * CHARBUFFSIZE)()
    return _rtnl_link_i2name(cache, link_index, name, sizeof(name))


def _link_address(link):
    """Returns the MAC address of a link object or None if the link does not
    have a physical address."""
    nl_addr = _rtnl_link_get_addr(link)
    if nl_addr:
        address = (c_char * HWADDRSIZE)()
        return _nl_addr2str(nl_addr, address, sizeof(address))
    else:
        return None


def _link_state(link):
    """Returns the textual representation of the link's operstate."""
    state = _rtnl_link_get_operstate(link)
    operstate = (c_char * CHARBUFFSIZE)()
    return _rtnl_link_operstate2str(state, operstate, sizeof(operstate))


# C function prototypes
# http://docs.python.org/2/library/ctypes.html#function-prototypes
# This helps ctypes know the calling conventions it should use to communicate
# with the binary interface of libnl and which types it should allocate and
# cast. Without it ctypes fails when not running on the main thread.
if _ethtool_uses_libnl3():
    _link_alloc_cache = CFUNCTYPE(c_int, c_void_p, c_int, c_void_p)(
        ('rtnl_link_alloc_cache', LIBNL_ROUTE))
    _link_is_vlan = _int_proto(('rtnl_link_is_vlan', LIBNL_ROUTE))
    _vlan_get_id = _int_proto(('rtnl_link_vlan_get_id', LIBNL_ROUTE))
    _rtnl_link_get_type = _char_proto(('rtnl_link_get_type', LIBNL_ROUTE))

    def _rtnl_link_alloc_cache(sock):
        """Wraps the new link alloc cache to expose the libnl1 signature"""
        AF_UNSPEC = 0
        cache = c_void_p()
        err = _link_alloc_cache(sock, AF_UNSPEC, byref(cache))
        if err:
            raise IOError(-err, _nl_geterror())
        return cache

    def _rtnl_link_vlan_get_id(link):
        """Wraps the new vlan id retrieval to expose the libnl1 signature"""
        if _link_is_vlan(link):
            return _vlan_get_id(link)
        else:
            return -1
else:
    from . import _alloc_cache
    _link_alloc_cache = _void_proto(('rtnl_link_alloc_cache', LIBNL_ROUTE))
    _rtnl_link_alloc_cache = partial(_alloc_cache, _link_alloc_cache)
    _rtnl_link_vlan_get_id = _int_proto(('rtnl_link_vlan_get_id', LIBNL_ROUTE))

_nl_link_cache = partial(_cache_manager, _rtnl_link_alloc_cache)

_rtnl_link_get_addr = _void_proto(('rtnl_link_get_addr', LIBNL_ROUTE))
_rtnl_link_get_flags = _int_proto(('rtnl_link_get_flags', LIBNL_ROUTE))
_rtnl_link_get_ifindex = _int_proto(('rtnl_link_get_ifindex', LIBNL_ROUTE))
_rtnl_link_get_link = _int_proto(('rtnl_link_get_link', LIBNL_ROUTE))
_rtnl_link_get_master = _int_proto(('rtnl_link_get_master', LIBNL_ROUTE))
_rtnl_link_get_mtu = _int_proto(('rtnl_link_get_mtu', LIBNL_ROUTE))
_rtnl_link_get_name = _char_proto(('rtnl_link_get_name', LIBNL_ROUTE))
_rtnl_link_get_operstate = _int_proto(('rtnl_link_get_operstate', LIBNL_ROUTE))
_rtnl_link_get_qdisc = _char_proto(('rtnl_link_get_qdisc', LIBNL_ROUTE))
_rtnl_link_get_by_name = CFUNCTYPE(c_void_p, c_void_p, c_char_p)((
    'rtnl_link_get_by_name', LIBNL_ROUTE))
_rtnl_link_i2name = CFUNCTYPE(c_char_p, c_void_p, c_int, c_char_p, c_size_t)((
    'rtnl_link_i2name', LIBNL_ROUTE))
_rtnl_link_operstate2str = _int_char_proto(('rtnl_link_operstate2str',
                                            LIBNL_ROUTE))
