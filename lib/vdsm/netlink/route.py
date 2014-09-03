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
from ctypes import CFUNCTYPE, c_int, c_void_p, byref
from functools import partial
from socket import AF_UNSPEC

from . import _cache_manager, _nl_cache_get_first, _nl_cache_get_next
from . import _char_proto, _int_proto, _void_proto
from . import LIBNL, LIBNL_ROUTE, _nl_geterror, _pool
from . import _addr_to_str, _af_to_str, _scope_to_str
from .link import _nl_link_cache, _link_index_to_name

_RT_TABLE_COMPAT = 252
_RT_TABLE_MAIN = 254


def iter_routes():
    """Generator that yields an information dictionary for each route in the
    system."""
    with _pool.socket() as sock:
        with _nl_route_cache(sock) as route_cache:
            with _nl_link_cache(sock) as link_cache:  # for index to label
                route = _nl_cache_get_first(route_cache)
                while route:
                    yield _route_info(link_cache, route)
                    route = _nl_cache_get_next(route)


def _route_info(link_cache, route):
    data = {
        'destination': _addr_to_str(_rtnl_route_get_dst(route)),  # network
        'source': _addr_to_str(_rtnl_route_get_src(route)),
        'gateway': _addr_to_str(_rtnl_route_get_gateway(route)),  # via
        'family': _af_to_str(_rtnl_route_get_family(route)),
        'scope': _scope_to_str(_rtnl_route_get_scope(route))}
    oif_index = _rtnl_route_get_oif(route)
    if oif_index > 0:
        data['oif'] = _link_index_to_name(link_cache, oif_index)
    table = _rtnl_route_get_table(route)
    if LIBNL != LIBNL_ROUTE or table != _RT_TABLE_COMPAT:
        data['table'] = table
    return data

if LIBNL != LIBNL_ROUTE:
    _route_alloc_cache = CFUNCTYPE(c_int, c_void_p, c_int, c_int, c_void_p)(
        ('rtnl_route_alloc_cache', LIBNL_ROUTE))
    _route_get_nnexthops = _int_proto(('rtnl_route_get_nnexthops',
                                       LIBNL_ROUTE))
    _route_get_nexthop_n = CFUNCTYPE(c_void_p, c_void_p, c_int)(
        ('rtnl_route_nexthop_n', LIBNL_ROUTE))
    _hop_get_ifindex = _int_proto(('rtnl_route_nh_get_ifindex', LIBNL_ROUTE))
    _hop_get_gateway = _void_proto(('rtnl_route_nh_get_gateway', LIBNL_ROUTE))

    def _rtnl_route_alloc_cache(sock):
        """Wraps the new addr alloc cache to expose the libnl1 signature"""
        cache = c_void_p()
        err = _route_alloc_cache(sock, AF_UNSPEC, 0, byref(cache))
        if err:
            raise IOError(-err, _nl_geterror())
        return cache

    def _route_get_next_hop(route):
        if _route_get_nnexthops(route) != 1:
            return
        return _route_get_nexthop_n(route, 0)

    def _rtnl_route_get_oif(route):
        hop = _route_get_next_hop(route)
        if hop is None:
            return -1
        else:
            return _hop_get_ifindex(hop)

    def _rtnl_route_get_gateway(route):
        hop = _route_get_next_hop(route)
        if hop is None:
            return None
        else:
            gw = _hop_get_gateway(hop)
            return gw

else:
    from . import _alloc_cache
    _route_alloc_cache = _void_proto(('rtnl_route_alloc_cache', LIBNL_ROUTE))
    _rtnl_route_get_gateway = _void_proto(('rtnl_route_get_gateway',
                                           LIBNL_ROUTE))
    _rtnl_route_get_oif = _int_proto(('rtnl_route_get_oif', LIBNL_ROUTE))
    _rtnl_route_alloc_cache = partial(_alloc_cache, _route_alloc_cache)


_nl_route_cache = partial(_cache_manager, _rtnl_route_alloc_cache)

_rtnl_route_get_dst = _void_proto(('rtnl_route_get_dst', LIBNL_ROUTE))
_rtnl_route_get_src = _void_proto(('rtnl_route_get_src', LIBNL_ROUTE))
_rtnl_route_get_iif = _char_proto(('rtnl_route_get_iif', LIBNL_ROUTE))
_rtnl_route_get_table = _int_proto(('rtnl_route_get_table', LIBNL_ROUTE))
_rtnl_route_get_scope = _int_proto(('rtnl_route_get_scope', LIBNL_ROUTE))
_rtnl_route_get_family = _int_proto(('rtnl_route_get_family', LIBNL_ROUTE))
