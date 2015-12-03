#
# Copyright 2015 Hat, Inc.
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
import shlex

from ..ipwrapper import getLinks


_IFCFG_ZERO_SUFFIXED = frozenset(
    ('IPADDR0', 'GATEWAY0', 'PREFIX0', 'NETMASK0'))
# TODO: once the unification of vdsm under site-packges is done, this duplicate
# TODO: of ifcfg.NET_CONF_DIR and ifcfg.NET_CONF_PREF can be removed
NET_CONF_DIR = '/etc/sysconfig/network-scripts/'
NET_CONF_PREF = NET_CONF_DIR + 'ifcfg-'


def getIfaceCfg(iface):
    ifaceCfg = {}
    try:
        with open(NET_CONF_PREF + iface) as f:
            for line in shlex.split(f, comments=True):
                k, v = line.split('=', 1)
                if k in _IFCFG_ZERO_SUFFIXED:
                    k = k[:-1]
                ifaceCfg[k] = v
    except Exception:
        pass
    return ifaceCfg


def visible_devs(predicate):
    """Returns a list of visible (vdsm manageable) links for which the
    predicate is True"""
    return [dev.name for dev in getLinks() if predicate(dev) and
            not dev.isHidden()]
