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
import errno
import os
from distutils.version import StrictVersion

from vdsm import netinfo

from .. import tc
from .. import models
_NON_VLANNED_ID = 5000
_DEFAULT_CLASSID = '%x' % _NON_VLANNED_ID
_ROOT_QDISC_HANDLE = '%x:' % 5001  # Leave 0 free for leaf qdisc of vlan tag 0
_FAIR_QDISC_KIND = 'fq_codel' if (StrictVersion(os.uname()[2].split('-')[0]) >
                                  StrictVersion('3.5.0')) else 'sfq'
_SHAPING_QDISC_KIND = 'hfsc'


def configure_outbound(qosOutbound, top_device):
    """Adds the qosOutbound configuration to the backing device (be it bond
    or nic). Adds a class and filter for default traffic if necessary"""
    vlan_tag = models.hierarchy_vlan_tag(top_device)
    device = models.hierarchy_backing_device(top_device).name
    root_qdisc = _root_qdisc(tc._qdiscs(device))
    class_id = '%x' % (_NON_VLANNED_ID if vlan_tag is None else vlan_tag)
    if not root_qdisc or root_qdisc['kind'] != _SHAPING_QDISC_KIND:
        _fresh_qdisc_conf_out(device, vlan_tag, class_id,
                              _qos_to_str_dict(qosOutbound))
    else:
        _qdisc_conf_out(device, root_qdisc['handle'], vlan_tag, class_id,
                        _qos_to_str_dict(qosOutbound))


def remove_outbound(top_device):
    """Removes the qosOutbound configuration from the device and restores
    pfifo_fast if it was the last QoSed network on the device"""
    vlan_tag = models.hierarchy_vlan_tag(top_device)
    device = models.hierarchy_backing_device(top_device).name
    class_id = '%x' % (_NON_VLANNED_ID if vlan_tag is None else vlan_tag)
    MISSING_OBJ_ERR_CODES = (errno.EINVAL, errno.ENOENT, errno.EOPNOTSUPP)

    try:
        tc.filter.delete(
            device, pref=_NON_VLANNED_ID if vlan_tag is None else vlan_tag)
    except tc.TrafficControlException as tce:
        if tce.errCode not in MISSING_OBJ_ERR_CODES:  # No filter exists
            raise

    device_qdiscs = list(tc._qdiscs(device))
    if not device_qdiscs:
        return
    root_qdisc_handle = _root_qdisc(device_qdiscs)['handle']
    try:
        tc.cls.delete(device, classid=root_qdisc_handle + class_id)
    except tc.TrafficControlException as tce:
        if tce.errCode not in MISSING_OBJ_ERR_CODES:  # No class exists
            raise

    if not _uses_classes(device, root_qdisc_handle=root_qdisc_handle):
        try:
            tc._qdisc_del(device)
            tc._qdisc_del(device, kind='ingress')
        except tc.TrafficControlException as tce:
            if tce.errCode not in MISSING_OBJ_ERR_CODES:  # No qdisc
                raise


def _uses_classes(device, root_qdisc_handle=None):
    """Returns true iff there's traffic classes in the device, ignoring the
    root class and a default unused class"""
    if root_qdisc_handle is None:
        root_qdisc_handle = _root_qdisc(tc._qdiscs(device))['handle']
    classes = [cls for cls in tc._classes(device, parent=root_qdisc_handle) if
               not cls.get('root')]
    return (classes and
            not(len(classes) == 1 and not netinfo.ifaceUsed(device) and
                classes[0]['handle'] == root_qdisc_handle + _DEFAULT_CLASSID))


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
        if tce.errCode not in (errno.EINVAL,
                               errno.ENOENT):  # No ingress exists
            raise

    tc.qdisc.add(dev, _SHAPING_QDISC_KIND, handle='0x' + _ROOT_QDISC_HANDLE,
                 default='%#x' % _NON_VLANNED_ID)
    tc.qdisc.add(dev, 'ingress')

    # Add traffic classes
    _add_hfsc_cls(dev, _ROOT_QDISC_HANDLE, class_id, **qos)
    if class_id != _DEFAULT_CLASSID:  # We need to add a default class
        _add_hfsc_cls(dev, _ROOT_QDISC_HANDLE, _DEFAULT_CLASSID, ls=qos['ls'])

    # Add filters to move the traffic into the classes we just created
    _add_non_vlanned_filter(dev, _ROOT_QDISC_HANDLE)
    if class_id != _DEFAULT_CLASSID:
        _add_vlan_filter(dev, vlan_tag, _ROOT_QDISC_HANDLE, class_id)

    # Add inside intra-class fairness qdisc (fq_codel/sfq)
    _add_fair_qdisc(dev, _ROOT_QDISC_HANDLE, class_id)
    if class_id != _DEFAULT_CLASSID:
        _add_fair_qdisc(dev, _ROOT_QDISC_HANDLE, _DEFAULT_CLASSID)


def _qdisc_conf_out(dev, root_qdisc_handle, vlan_tag, class_id, qos):
    """Adds the traffic class and filtering to the current hfsc qdisc"""
    flow_id = ':' + class_id
    filters = [filt for filt in tc._filters(dev, parent=root_qdisc_handle) if
               'u32' in filt and filt['u32'].get('flowid') == flow_id]

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
        _add_vlan_filter(dev, vlan_tag, root_qdisc_handle, class_id)
    _add_fair_qdisc(dev, root_qdisc_handle, class_id)


def _add_vlan_filter(dev, vlan_tag, root_qdisc_handle, class_id):
    tc.filter.replace(dev, parent=root_qdisc_handle, protocol='802.1q',
                      pref=vlan_tag,
                      u32=['match', 'u16', '0x%x' % vlan_tag, '0xFFF', 'at',
                           '-4', 'flowid', '0x' + class_id])


def _add_non_vlanned_filter(dev, root_qdisc_handle):
    tc.filter.replace(dev, parent=root_qdisc_handle, protocol='all',
                      pref=_NON_VLANNED_ID,
                      u32=['match', 'u8', '0', '0', 'flowid',
                           '%#x' % _NON_VLANNED_ID])


def _add_fair_qdisc(dev, root_qdisc_handle, class_id):
    tc.qdisc.add(dev, _FAIR_QDISC_KIND, parent=root_qdisc_handle + class_id,
                 handle=class_id + ':')


def _add_hfsc_cls(dev, root_qdisc_handle, class_id, **qos_opts):
    tc.cls.add(dev, _SHAPING_QDISC_KIND, parent=root_qdisc_handle,
               classid=root_qdisc_handle + class_id, **qos_opts)


def _qos_to_str_dict(qos):
    data = {}
    for curve, attrs in qos.items():
        data[curve] = []
        if 'm1' in attrs:
            data[curve] += ['m1', '%sbit' % attrs.get('m1', 0),
                            'd', '%sus' % attrs.get('d', 0)]
        if 'm2' in attrs:
            data[curve] += ['m2', '%sbit' % attrs.get('m2', 0)]
    return data


def _root_qdisc(qdiscs):
    for qdisc in qdiscs:
        if 'root' in qdisc:
            return qdisc
