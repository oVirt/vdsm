# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from collections import defaultdict
import logging

import six

from vdsm.network import tc

NON_VLANNED_ID = 5000
DEFAULT_CLASSID = '%x' % NON_VLANNED_ID


def report_network_qos(nets_info, devs_info):
    """Augment netinfo information with QoS data for the engine"""
    qdiscs = defaultdict(list)
    for qdisc in tc.qdiscs(dev=None):  # None -> all dev qdiscs
        qdiscs[qdisc['dev']].append(qdisc)
    for net, attrs in six.viewitems(nets_info):
        iface = attrs['iface']
        if iface in devs_info['bridges']:
            host_ports = [
                port for port in attrs['ports'] if not port.startswith('vnet')
            ]
            if not host_ports:  # Port-less bridge
                continue
            if len(host_ports) > 1:
                logging.error(
                    'Multiple southbound ports per network detected,'
                    ' ignoring this network for the QoS report '
                    '(network: %s, ports: %s)',
                    net,
                    host_ports,
                )
                continue
            (iface,) = host_ports
        if iface in devs_info['vlans']:
            vlan_id = devs_info['vlans'][iface]['vlanid']
            iface = devs_info['vlans'][iface]['iface']
            iface_qdiscs = qdiscs.get(iface)
            if iface_qdiscs is None:
                continue
            class_id = get_root_qdisc(iface_qdiscs)['handle'] + '%x' % vlan_id
        else:
            iface_qdiscs = qdiscs.get(iface)
            if iface_qdiscs is None:
                continue
            class_id = get_root_qdisc(iface_qdiscs)['handle'] + DEFAULT_CLASSID

        # Now that iface is either a bond or a nic, let's get the QoS info
        classes = [
            cls
            for cls in tc.classes(iface, classid=class_id)
            if cls['kind'] == 'hfsc'
        ]
        if classes:
            (cls,) = classes
            attrs['hostQos'] = {'out': cls['hfsc']}


def get_root_qdisc(qdiscs):
    for qdisc in qdiscs:
        if 'root' in qdisc:
            return qdisc
