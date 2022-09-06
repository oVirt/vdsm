# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
