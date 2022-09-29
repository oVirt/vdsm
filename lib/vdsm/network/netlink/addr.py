# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from functools import partial
import errno

from . import _cache_manager
from . import _pool
from . import libnl
from .link import _nl_link_cache, _link_index_to_name


def iter_addrs():
    """Generator that yields an information dictionary for each network address
    in the system."""
    with _pool.socket() as sock:
        with _nl_addr_cache(sock) as addr_cache:
            with _nl_link_cache(sock) as link_cache:  # for index to label
                addr = libnl.nl_cache_get_first(addr_cache)
                while addr:
                    yield _addr_info(addr, link_cache=link_cache)
                    addr = libnl.nl_cache_get_next(addr)


def _addr_info(addr, link_cache=None):
    """Returns a dictionary with the address information."""
    index = libnl.rtnl_addr_get_ifindex(addr)
    local_address = libnl.rtnl_addr_get_local(addr)
    data = {
        'index': index,
        'family': libnl.nl_af2str(libnl.rtnl_addr_get_family(addr)),
        'prefixlen': libnl.rtnl_addr_get_prefixlen(addr),
        'scope': libnl.rtnl_scope2str(libnl.rtnl_addr_get_scope(addr)),
        'flags': _addr_flags(addr),
        'address': libnl.nl_addr2str(local_address) if local_address else None,
    }
    try:
        data['label'] = _link_index_to_name(index, cache=link_cache)
    except IOError as err:
        if err.errno != errno.ENODEV:
            raise
    return data


def split(addr):
    """Split an addr dict from iter_addrs"""
    # for 32bits address, the address field is slashless
    return addr['address'].split('/')[0], addr['prefixlen']


def cidr_form(addr):
    return '{}/{}'.format(*split(addr))


def is_primary(addr):
    return 'secondary' not in addr['flags']


def is_permanent(addr):
    return 'permanent' in addr['flags']


def _addr_flags(addr):
    """Returns the textual representation of the address flags"""
    return frozenset(
        libnl.rtnl_addr_flags2str(libnl.rtnl_addr_get_flags(addr)).split(',')
    )


_nl_addr_cache = partial(_cache_manager, libnl.rtnl_addr_alloc_cache)
