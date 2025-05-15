# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from collections import defaultdict
import itertools
import logging

from vdsm.network.ipwrapper import IPRoute2Error
from vdsm.network.ipwrapper import routeGet, Route, routeShowGateways
from vdsm.network.ipwrapper import route6_show_gateways
from vdsm.network.netlink import route as nl_route
from vdsm.network.netlink.libnl import RtKnownTables


def getRouteDeviceTo(destinationIP):
    """Return the name of the device leading to destinationIP or the empty
    string if none is found"""
    try:
        route = routeGet([destinationIP])[0]
    except (IPRoute2Error, IndexError):
        logging.exception('Could not route to %s', destinationIP)
        return ''

    try:
        return Route.fromText(route).device
    except ValueError:
        logging.exception('Could not parse route %s', route)
        return ''


def getDefaultGateway():
    output = routeShowGateways('main')
    return Route.fromText(output[0]) if output else None


def ipv6_default_gateway():
    output = route6_show_gateways('main')
    return Route.fromText(output[0]) if output else None


def is_default_route(gateway, routes):
    if not gateway:
        return False

    for route in itertools.chain.from_iterable(routes.values()):
        if (
            route.get('table') == RtKnownTables.RT_TABLE_MAIN
            and route['family'] == 'inet'
            and route['scope'] == 'global'
            and route['gateway'] == gateway
            and route['destination'] in ('none', '0.0.0.0/0', '::/0')
        ):
            return True
    return False


def is_ipv6_default_route(gateway):
    if not gateway:
        return False

    dg = ipv6_default_gateway()
    return (gateway == dg.via) if dg else False


def get_gateway(
    routes_by_dev, dev, family=4, table=RtKnownTables.RT_TABLE_UNSPEC
):
    """
    Return the default gateway for a device and an address family
    :param routes_by_dev: dictionary from device names to a list of routes.
    :type routes_by_dev: dict[str]->list[dict[str]->str]
    """
    routes = routes_by_dev[dev]

    # VDSM's source routing thread creates a separate table (with an ID derived
    # currently from an IPv4 address) for each device so we have to look for
    # the gateway in all tables (RT_TABLE_UNSPEC), not just the 'main' one.
    gateways = [
        r
        for r in routes
        if r['destination'] in ('none', '0.0.0.0/0', '::/0')
        and (r.get('table') == table or table == RtKnownTables.RT_TABLE_UNSPEC)
        and r['scope'] == 'global'
        and r['family'] == ('inet6' if family == 6 else 'inet')
    ]
    if not gateways:
        return '::' if family == 6 else ''
    elif len(gateways) == 1:
        return gateways[0]['gateway']
    else:
        unique_gateways = frozenset(route['gateway'] for route in gateways)
        if len(unique_gateways) == 1:
            (gateway,) = unique_gateways
            logging.debug(
                'The gateway %s is duplicated for the device %s', gateway, dev
            )
            return gateway
        else:
            # We could pick the first gateway or the one with the lowest metric
            # but, in general, there are also routing rules in the game so we
            # should probably ask the kernel somehow.
            logging.error(
                'Multiple IPv%s gateways for the device %s in table ' '%s: %r',
                family,
                dev,
                table,
                gateways,
            )
            return '::' if family == 6 else ''


def get_routes():
    """Returns all the routes data dictionaries"""
    routes = defaultdict(list)
    for route in nl_route.iter_routes():
        oif = route.get('oif')
        if oif is not None:
            routes[oif].append(route)
    return routes
