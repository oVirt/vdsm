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

from contextlib import contextmanager
from ctypes import (CDLL, CFUNCTYPE, c_char, c_char_p, c_int, c_void_p,
                    c_size_t, get_errno, sizeof)
import errno

NETLINK_ROUTE = 0
CHARBUFFSIZE = 32
LIBNL = CDLL('libnl.so.1', use_errno=True)

# C function prototypes
# http://docs.python.org/2/library/ctypes.html#function-prototypes
# This helps ctypes know the calling conventions it should use to communicate
# with the binary interface of libnl and which types it should allocate and
# cast. Without it ctypes fails when not running on the main thread.
_int_proto = CFUNCTYPE(c_int, c_void_p)
_char_proto = CFUNCTYPE(c_char_p, c_void_p)
_void_proto = CFUNCTYPE(c_void_p, c_void_p)

_nl_connect = CFUNCTYPE(c_int, c_void_p, c_int)(('nl_connect', LIBNL))
_nl_handle_alloc = CFUNCTYPE(c_void_p)(('nl_handle_alloc', LIBNL))
_nl_handle_destroy = CFUNCTYPE(None, c_void_p)(('nl_handle_destroy', LIBNL))

_nl_cache_free = CFUNCTYPE(None, c_void_p)(('nl_cache_free', LIBNL))
_nl_cache_get_first = _void_proto(('nl_cache_get_first', LIBNL))
_nl_cache_get_next = _void_proto(('nl_cache_get_next', LIBNL))
_rtnl_link_alloc_cache = _void_proto(('rtnl_link_alloc_cache', LIBNL))

_rtnl_link_get_addr = _void_proto(('rtnl_link_get_addr', LIBNL))
_rtnl_link_get_flags = _int_proto(('rtnl_link_get_flags', LIBNL))
_rtnl_link_get_ifindex = _int_proto(('rtnl_link_get_ifindex', LIBNL))
_rtnl_link_get_link = _int_proto(('rtnl_link_get_link', LIBNL))
_rtnl_link_get_master = _int_proto(('rtnl_link_get_master', LIBNL))
_rtnl_link_get_mtu = _int_proto(('rtnl_link_get_mtu', LIBNL))
_rtnl_link_get_name = _char_proto(('rtnl_link_get_name', LIBNL))
_rtnl_link_get_operstate = _int_proto(('rtnl_link_get_operstate', LIBNL))
_rtnl_link_get_qdisc = _char_proto(('rtnl_link_get_qdisc', LIBNL))
_rtnl_link_vlan_get_id = _int_proto(('rtnl_link_vlan_get_id', LIBNL))

_nl_addr2str = CFUNCTYPE(c_char_p, c_void_p, c_char_p, c_int)((
    'nl_addr2str', LIBNL))
_rtnl_link_get_by_name = CFUNCTYPE(c_void_p, c_void_p, c_char_p)((
    'rtnl_link_get_by_name', LIBNL))
_rtnl_link_i2name = CFUNCTYPE(c_char_p, c_void_p, c_int, c_char_p, c_int)((
    'rtnl_link_i2name', LIBNL))
_rtnl_link_operstate2str = CFUNCTYPE(c_char_p, c_int, c_char_p, c_size_t)((
    'rtnl_link_operstate2str', LIBNL))


def iter_links():
    """Generator that yields an information dictionary for each link of the
    system."""
    with _nl_link_cache() as cache:
        link = _nl_cache_get_first(cache)
        while link:
            yield _link_info(cache, link)
            link = _nl_cache_get_next(link)


def get_link(name):
    """Returns the information dictionary of the name specified link."""
    with _nl_link_cache() as cache:
        link = _rtnl_link_get_by_name(cache, name)
        if not link:
            raise IOError(errno.ENODEV, '%s is not present in the system' %
                          name)
        return _link_info(cache, link)


@contextmanager
def _open_nl_socket():
    """Provides a Netlink socket and closes and destroys it upon exit."""
    handle = _nl_handle_alloc()
    if handle is None:
        raise IOError(get_errno(), 'Failed to allocate netlink handle')
    try:
        err = _nl_connect(handle, NETLINK_ROUTE)
        if err:
            raise IOError(-err, 'Failed to connect to netlink socket.')
        yield handle
    finally:
        # handle is automatically disconnected on destroy.
        _nl_handle_destroy(handle)


@contextmanager
def _nl_link_cache():
    """Provides a link cache and frees it and its links upon exit."""
    with _open_nl_socket() as sock:
        cache = _rtnl_link_alloc_cache(sock)
        if cache is None:
            raise IOError(get_errno(), 'Failed to allocate link cache.')
        try:
            yield cache
        finally:
            _nl_cache_free(cache)


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
    # TODO: Add the following line (and prototype) when the above bug is fixed.
    # info['type'] = _rtnl_link_get_info_type(link)

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
        address = (c_char * CHARBUFFSIZE)()
        return _nl_addr2str(nl_addr, address, sizeof(address))
    else:
        return None


def _link_state(link):
    """Returns the textual representation of the link's operstate."""
    state = _rtnl_link_get_operstate(link)
    operstate = (c_char * CHARBUFFSIZE)()
    return _rtnl_link_operstate2str(state, operstate, sizeof(operstate))
