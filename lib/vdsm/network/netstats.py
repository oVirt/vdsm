# Copyright 2018 Red Hat, Inc.
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

from time import time as current_time_since_epoch

import six

from vdsm.network.link import stats as link_stats


def report():
    rx_dropped = tx_dropped = 0
    stats = link_stats.report()
    timestamp = current_time_since_epoch()
    for iface_stats in six.viewvalues(stats):
        iface_stats['sampleTime'] = timestamp

        rx_dropped += iface_stats['rxDropped']
        tx_dropped += iface_stats['txDropped']

        _normalize_network_stats(iface_stats)

    return {'network': stats, 'rxDropped': tx_dropped, 'txDropped': rx_dropped}


def _normalize_network_stats(iface_stats):
    """
    For backward compatability, the network stats are normalized to fit
    expected format/type on Engine side.
    """
    iface_stats['speed'] = iface_stats['speed'] or 1000
    for stat_name in (
        'speed',
        'tx',
        'rx',
        'rxErrors',
        'txErrors',
        'rxDropped',
        'txDropped',
    ):
        iface_stats[stat_name] = str(iface_stats[stat_name])
