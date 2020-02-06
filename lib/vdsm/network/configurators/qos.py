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

from __future__ import absolute_import
from __future__ import division
import errno

import six

from vdsm.network import tc
from vdsm.network.netinfo import qos as netinfo_qos
from vdsm.network.netinfo.cache import NetInfo, get as cache_get

_ROOT_QDISC_HANDLE = '%x:' % 5001  # Leave 0 free for leaf qdisc of vlan tag 0
_FAIR_QDISC_KIND = 'fq_codel'
_SHAPING_QDISC_KIND = 'hfsc'
_DEFAULT_CLASSID = netinfo_qos.DEFAULT_CLASSID  # shorthand
_NON_VLANNED_ID = netinfo_qos.NON_VLANNED_ID  # shorthand


def configure_outbound(qosOutbound, device, vlan_tag):
    """Adds the qosOutbound configuration to the backing device (be it bond
    or nic). Adds a class and filter for default traffic if necessary. vlan_tag
    can be None"""
    root_qdisc = netinfo_qos.get_root_qdisc(tc.qdiscs(device))
    class_id = '%x' % (_NON_VLANNED_ID if vlan_tag is None else vlan_tag)
    if not root_qdisc or root_qdisc['kind'] != _SHAPING_QDISC_KIND:
        _fresh_qdisc_conf_out(device, vlan_tag, class_id, qosOutbound)
    else:
        _qdisc_conf_out(
            device, root_qdisc['handle'], vlan_tag, class_id, qosOutbound
        )


def remove_outbound(device, vlan_tag, net_info):
    """Removes the qosOutbound configuration from the device and restores
    pfifo_fast if it was the last QoSed network on the device. vlan_tag
    can be None"""
    class_id = '%x' % (_NON_VLANNED_ID if vlan_tag is None else vlan_tag)
    MISSING_OBJ_ERR_CODES = (errno.EINVAL, errno.ENOENT, errno.EOPNOTSUPP)

    try:
        tc.filter.delete(
            device, pref=_NON_VLANNED_ID if vlan_tag is None else vlan_tag
        )
    except tc.TrafficControlException as tce:
        if tce.errCode not in MISSING_OBJ_ERR_CODES:  # No filter exists
            raise

    device_qdiscs = list(tc.qdiscs(device))
    if not device_qdiscs:
        return
    root_qdisc_handle = netinfo_qos.get_root_qdisc(device_qdiscs)['handle']
    try:
        tc.cls.delete(device, classid=root_qdisc_handle + class_id)
    except tc.TrafficControlException as tce:
        if tce.errCode not in MISSING_OBJ_ERR_CODES:  # No class exists
            raise

    if not _uses_classes(
        device, net_info, root_qdisc_handle=root_qdisc_handle
    ):
        try:
            tc._qdisc_del(device)
            tc._qdisc_del(device, kind='ingress')
        except tc.TrafficControlException as tce:
            if tce.errCode not in MISSING_OBJ_ERR_CODES:  # No qdisc
                raise


def _uses_classes(device, net_info, root_qdisc_handle=None):
    """Returns true iff there's traffic classes in the device, ignoring the
    root class and a default unused class"""
    if root_qdisc_handle is None:
        root_qdisc = netinfo_qos.get_root_qdisc(tc.qdiscs(device))
        root_qdisc_handle = root_qdisc['handle']
    classes = [
        cls
        for cls in tc.classes(device, parent=root_qdisc_handle)
        if not cls.get('root')
    ]
    return classes and not (
        len(classes) == 1
        and not net_info.ifaceUsers(device)
        and classes[0]['handle'] == root_qdisc_handle + _DEFAULT_CLASSID
    )


def _fresh_qdisc_conf_out(dev, vlan_tag, class_id, qos):
    """Replaces the dev qdisc with hfsc and sets up the shaping"""
    # Use deletion + addition to flush children classes and filters
    try:
        tc.qdisc.delete(dev)  # Deletes the root qdisc by default
    except tc.TrafficControlException as tce:
        if tce.errCode != errno.ENOENT:
            raise
    try:
        tc.qdisc.delete(dev, kind='ingress')  # Deletes the ingress qdisc
    except tc.TrafficControlException as tce:
        if tce.errCode not in (
            errno.EINVAL,
            errno.ENOENT,
        ):  # No ingress exists
            raise

    tc.qdisc.add(
        dev,
        _SHAPING_QDISC_KIND,
        handle='0x' + _ROOT_QDISC_HANDLE,
        default='%#x' % _NON_VLANNED_ID,
    )
    tc.qdisc.add(dev, 'ingress')

    # Add traffic classes
    _add_hfsc_cls(dev, _ROOT_QDISC_HANDLE, class_id, **qos)
    if class_id != _DEFAULT_CLASSID:  # We need to add a default class
        _add_hfsc_cls(dev, _ROOT_QDISC_HANDLE, _DEFAULT_CLASSID, ls=qos['ls'])

    # Add filters to move the traffic into the classes we just created
    _add_non_vlanned_filter(dev, _ROOT_QDISC_HANDLE)
    if class_id != _DEFAULT_CLASSID:
        _add_vlan_filter(dev, vlan_tag, _ROOT_QDISC_HANDLE, class_id)

    # Add inside intra-class fairness qdisc (fq_codel)
    _add_fair_qdisc(dev, _ROOT_QDISC_HANDLE, class_id)
    if class_id != _DEFAULT_CLASSID:
        _add_fair_qdisc(dev, _ROOT_QDISC_HANDLE, _DEFAULT_CLASSID)


def _qdisc_conf_out(dev, root_qdisc_handle, vlan_tag, class_id, qos):
    """Adds the traffic class and filtering to the current hfsc qdisc"""
    flow_id = _ROOT_QDISC_HANDLE + class_id

    def filt_flow_id(filt, kind):
        return filt.get(kind, {}).get('flowid')

    filters = [
        filt
        for filt in tc._filters(dev, parent=root_qdisc_handle)
        if flow_id in (filt_flow_id(filt, 'basic'), filt_flow_id(filt, 'u32'))
    ]

    # Clear up any previous filters to the class
    for filt in filters:
        try:
            tc.filter.delete(dev, filt['pref'], parent=root_qdisc_handle)
        except tc.TrafficControlException as tce:
            if tce.errCode != errno.EINVAL:  # no filters exist -> EINVAL
                raise

    # Clear the class in case it exists
    try:
        tc.cls.delete(dev, classid=root_qdisc_handle + class_id)
    except tc.TrafficControlException as tce:
        if tce.errCode != errno.ENOENT:
            raise

    _add_hfsc_cls(dev, root_qdisc_handle, class_id, **qos)
    if class_id == _DEFAULT_CLASSID:
        _add_non_vlanned_filter(dev, root_qdisc_handle)
    else:
        if not _is_explicit_defined_default_class(dev):
            default_class, = [
                c['hfsc']
                for c in tc.classes(dev)
                if c['handle'] == _ROOT_QDISC_HANDLE + _DEFAULT_CLASSID
            ]
            ls_max_rate = _max_hfsc_ls_rate(dev)
            default_class['ls']['m2'] = ls_max_rate

            tc.cls.delete(dev, classid=_ROOT_QDISC_HANDLE + _DEFAULT_CLASSID)
            _add_hfsc_cls(
                dev,
                _ROOT_QDISC_HANDLE,
                _DEFAULT_CLASSID,
                ls=default_class['ls'],
            )
            _add_fair_qdisc(dev, _ROOT_QDISC_HANDLE, _DEFAULT_CLASSID)

        _add_vlan_filter(dev, vlan_tag, root_qdisc_handle, class_id)
    _add_fair_qdisc(dev, root_qdisc_handle, class_id)


def _add_vlan_filter(dev, vlan_tag, root_qdisc_handle, class_id):
    tc.filter.replace(
        dev,
        parent=root_qdisc_handle,
        protocol='all',
        pref=vlan_tag,
        basic=[
            'match',
            'meta(vlan eq %s)' % vlan_tag,
            'flowid',
            root_qdisc_handle + class_id,
        ],
    )


def _add_non_vlanned_filter(dev, root_qdisc_handle):
    tc.filter.replace(
        dev,
        parent=root_qdisc_handle,
        protocol='all',
        pref=_NON_VLANNED_ID,
        u32=['match', 'u8', '0', '0', 'flowid', '%#x' % _NON_VLANNED_ID],
    )


def _add_fair_qdisc(dev, root_qdisc_handle, class_id):
    tc.qdisc.add(
        dev,
        _FAIR_QDISC_KIND,
        parent=root_qdisc_handle + class_id,
        handle=class_id + ':',
    )


def _add_hfsc_cls(dev, root_qdisc_handle, class_id, **qos_opts):
    tc.cls.add(
        dev,
        _SHAPING_QDISC_KIND,
        parent=root_qdisc_handle,
        classid=root_qdisc_handle + class_id,
        **qos_opts
    )


def _is_explicit_defined_default_class(dev):
    """
    A default class is defined explicitly when a non-vlan network has hostQos
    definitions.
    """
    netinfo = NetInfo(cache_get())
    for attrs in six.viewvalues(netinfo.networks):
        if 'vlan' not in attrs and 'hostQos' in attrs:
            ports = attrs['ports'] if attrs['bridged'] else [attrs['iface']]
            if dev in ports:
                return True

    return False


def _max_hfsc_ls_rate(dev):
    return max(
        [
            cls['hfsc']['ls']['m2']
            for cls in tc.classes(dev)
            if cls['kind'] == 'hfsc' and 'root' not in cls
        ]
    )
