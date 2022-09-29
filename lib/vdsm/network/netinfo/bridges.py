# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import logging
import os

from vdsm.network.link import bridge as br

BRIDGING_OPT = '/sys/class/net/%s/bridge/%s'


def ports(bridge):
    brif_path = os.path.join('/sys/class/net', bridge, 'brif')
    if os.path.isdir(brif_path):
        bridge_ports = os.listdir(brif_path)
    else:
        # We expect "bridge" to be a Linux bridge with interfaces. It is quite
        # common that this is not the case, when the bridge is actually
        # implemented by OVS (via our hook) or when the Linux bridge device is
        # not yet up.
        logging.warning('%s is not a Linux bridge', bridge)
        bridge_ports = []
    return bridge_ports


def bridge_options(name):
    """Returns a dictionary of bridge option name and value. E.g.,
    {'max_age': '2000', 'gc_timer': '332'}"""
    bridge = br.Bridge(name)
    return bridge.options


def stp_state(bridge):
    with open(BRIDGING_OPT % (bridge, 'stp_state')) as stp_file:
        stp = stp_file.readline()
    if stp == '1\n':
        return 'on'
    else:
        return 'off'


def stp_booleanize(value):
    if value is None:
        return False
    if type(value) is bool:
        return value
    if value.lower() in ('true', 'on', 'yes'):
        return True
    elif value.lower() in ('false', 'off', 'no'):
        return False
    else:
        raise ValueError('Invalid value for bridge stp')


def info(link):
    return {'ports': ports(link.name), 'stp': stp_state(link.name)}
