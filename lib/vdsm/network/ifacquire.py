# Copyright 2016-2018 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import itertools
import logging
import os
import re

import six

from vdsm.common import fileutils
from .configurators import ifcfg
from .ip import address
from .ip import dhclient
from .link import iface as linkiface


ACQUIRED_IFCFG_TAG = u'# This device is now owned by VDSM.\n'
ACQUIRED_IFCFG_PREFIX = [
    ACQUIRED_IFCFG_TAG,
    '# Please do not do any changes here while the device is used by VDSM.\n',
    '# Once it is detached from VDSM, remove this prefix before applying\n',
    '# any changes.\n',
]


class Transaction(object):
    """Acquire external interfaces which are not owned by us yet.

    In case of an unexpected failure, rollback acquired ifcfg-persisted
    interfaces.
    """

    def __init__(self, netinfo_nets):
        self._owned_ports = frozenset(
            itertools.chain.from_iterable(
                [attrs['ports'] for attrs in six.itervalues(netinfo_nets)]
            )
        )
        self._ifaces = {}  # {name: ifcfg_lines OR None}

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if type is None:
            try:
                self._disable_onboot()
            except Exception:
                self._rollback()
                raise
        else:
            self._rollback()

    def acquire(self, ifaces):
        logging.debug('Acquiring ifaces: %s', ifaces)
        self._backup(ifaces)
        self._release_ifaces()

    def _rollback(self):
        logging.debug(
            'Acquiring transaction failed, ' 'reverting ifaces: %s',
            list(self._ifaces),
        )
        for iface, ifcfg_lines in six.iteritems(self._ifaces):
            if ifcfg_lines:
                _rollback_ifcfg_iface(iface, ifcfg_lines)

    def _backup(self, ifaces):
        for iface in ifaces:
            if iface not in self._owned_ports:
                self._ifaces[iface] = (
                    _get_ifcfg_config(iface)
                    if _is_ifcfg_controlled(iface)
                    else None
                )

    def _release_ifaces(self):
        for iface, ifcfg_lines in six.iteritems(self._ifaces):
            if ifcfg_lines:
                _release_ifcfg_iface(iface)
            else:
                _release_non_ifcfg_iface(iface)

    def _disable_onboot(self):
        for iface, ifcfg_lines in six.iteritems(self._ifaces):
            if ifcfg_lines:
                _disable_onboot_ifcfg_iface(iface)


def _is_ifcfg_controlled(iface):
    return os.path.isfile(ifcfg.NET_CONF_PREF + iface)


def _get_ifcfg_config(iface):
    with open(ifcfg.NET_CONF_PREF + iface) as f:
        return f.readlines()


def _rollback_ifcfg_iface(iface, ifcfg_lines):
    with fileutils.atomic_file_write(ifcfg.NET_CONF_PREF + iface, 'w') as f:
        f.writelines(ifcfg_lines)
    ifcfg.ifup(iface)


def _release_ifcfg_iface(iface):
    _set_ifcfg_param(iface, 'NM_CONTROLLED', 'no')
    ifcfg.ifdown(iface)


def _disable_onboot_ifcfg_iface(iface):
    _set_ifcfg_param(iface, 'ONBOOT', 'no')


def _set_ifcfg_param(iface, key, value):
    with fileutils.atomic_file_write(ifcfg.NET_CONF_PREF + iface, 'r+') as f:
        lines = f.readlines()
        lines = _mark_ifcfg_with_prefix(lines)

        line_index, current_value = _ifcfg_key_lookup(lines, key)
        if line_index is None:
            lines.append('{}={}  # Set by VDSM\n'.format(key, value))
        else:
            if current_value != value:
                lines[
                    line_index
                ] = '{}={}  # Changed by VDSM, original: {}'.format(
                    key, value, lines[line_index]
                )

        f.seek(0)
        f.writelines(lines)


def _mark_ifcfg_with_prefix(lines):
    if lines[0] == ACQUIRED_IFCFG_TAG:
        return lines
    else:
        return ACQUIRED_IFCFG_PREFIX + lines


def _ifcfg_key_lookup(lines, key):
    for i, line in enumerate(lines):
        parsed_line = re.split('=| |\n', line)
        if parsed_line[0] == key:
            return i, parsed_line[1]
    return None, None


def _release_non_ifcfg_iface(iface):
    if not linkiface.iface(iface).exists():
        return
    # TODO: Tell NetworkManager to unmanage this iface.
    dhclient.kill(iface, family=4)
    dhclient.kill(iface, family=6)
    address.flush(iface)
