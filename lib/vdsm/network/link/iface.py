# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import abc
import errno
import logging
import os
import random
import string

from vdsm.network import ethtool
from vdsm.network import ipwrapper
from vdsm.network.netlink import libnl
from vdsm.network.netlink import link
from vdsm.network.netlink.waitfor import waitfor_linkup


STATE_UP = 'up'
STATE_DOWN = 'down'

NET_PATH = '/sys/class/net'

DEFAULT_MTU = 1500


class Type(object):
    NIC = 'nic'
    VLAN = 'vlan'
    BOND = 'bond'
    BRIDGE = 'bridge'
    LOOPBACK = 'loopback'
    MACVLAN = 'macvlan'
    DUMMY = 'dummy'
    TUN = 'tun'
    OVS = 'openvswitch'
    TEAM = 'team'
    VETH = 'veth'
    VF = 'vf'


class IfaceAPI(object):
    """
    Link iface driver interface.
    """

    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def up(self, admin_blocking=True, oper_blocking=False):
        """
        Set link state to UP, optionally blocking on the action.
        :param dev: iface name.
        :param admin_blocking: Block until the administrative state is UP.
        :param oper_blocking: Block until the link is operational.
        admin state is at kernel level, while link state is at driver level.
        """

    @abc.abstractmethod
    def down(self):
        pass

    @abc.abstractmethod
    def properties(self):
        pass

    @abc.abstractmethod
    def is_up(self):
        pass

    @abc.abstractmethod
    def is_admin_up(self):
        pass

    @abc.abstractmethod
    def is_oper_up(self):
        pass

    @abc.abstractmethod
    def is_promisc(self):
        pass

    @abc.abstractmethod
    def exists(self):
        pass

    @abc.abstractmethod
    def address(self):
        pass

    @abc.abstractmethod
    def set_address(self, address):
        pass

    @abc.abstractmethod
    def mtu(self):
        pass

    @abc.abstractmethod
    def type(self):
        pass

    @abc.abstractmethod
    def statistics(self):
        pass


class IfaceHybrid(IfaceAPI):
    """
    Link iface driver implemented by a mix of iproute2, netlink and sysfs.
    """

    def __init__(self):
        self._dev = None
        self._vfid = None

    @property
    def device(self):
        return self._dev

    @device.setter
    def device(self, dev):
        if self._dev:
            raise AttributeError('Constant attribute, unable to modify')
        self._dev = dev

    @property
    def vfid(self):
        return self._vfid

    @vfid.setter
    def vfid(self, vf):
        if self._vfid:
            raise AttributeError('Constant attribute, unable to modify')
        self._vfid = vf

    def properties(self):
        return link.get_link(self._dev)

    def up(self, admin_blocking=True, oper_blocking=False):
        if admin_blocking:
            self._up_blocking(oper_blocking)
        else:
            ipwrapper.linkSet(self._dev, [STATE_UP])

    def down(self):
        ipwrapper.linkSet(self._dev, [STATE_DOWN])

    def is_up(self):
        return self.is_admin_up()

    def is_admin_up(self):
        properties = self.properties()
        return link.is_link_up(properties['flags'], check_oper_status=False)

    def is_oper_up(self):
        properties = self.properties()
        return link.is_link_up(properties['flags'], check_oper_status=True)

    def is_promisc(self):
        properties = self.properties()
        return bool(properties['flags'] & libnl.IfaceStatus.IFF_PROMISC)

    def exists(self):
        return os.path.exists(os.path.join(NET_PATH, self._dev))

    def address(self):
        return self.properties()['address']

    def set_address(self, address):
        if self._vfid is None:
            link_set_args = ['address', address]
        else:
            link_set_args = ['vf', str(self._vfid), 'mac', address]
        ipwrapper.linkSet(self._dev, link_set_args)

    def mtu(self):
        return self.properties()['mtu']

    def type(self):
        return self.properties().get('type', get_alternative_type(self._dev))

    def statistics(self):
        return {
            'name': self.device,
            'rx': _get_stat(self.device, 'rx_bytes'),
            'tx': _get_stat(self.device, 'tx_bytes'),
            'state': 'up' if self.is_oper_up() else 'down',
            'rxDropped': _get_stat(self.device, 'rx_dropped'),
            'txDropped': _get_stat(self.device, 'tx_dropped'),
            'rxErrors': _get_stat(self.device, 'rx_errors'),
            'txErrors': _get_stat(self.device, 'tx_errors'),
        }

    def _up_blocking(self, link_blocking):
        with waitfor_linkup(self._dev, link_blocking):
            ipwrapper.linkSet(self._dev, [STATE_UP])


def iface(device, vfid=None) -> IfaceHybrid:
    """Iface factory"""
    interface = IfaceHybrid()
    interface.device = device
    interface.vfid = vfid
    return interface


def list():
    for properties in link.iter_links():
        if 'type' not in properties:
            properties['type'] = get_alternative_type(properties['name'])
        yield properties


def random_iface_name(prefix='', max_length=15, digit_only=False):
    """
    Create a network device name with the supplied prefix and a pseudo-random
    suffix, e.g. dummy_ilXaYiSn7. The name is bound to IFNAMSIZ of 16-1 chars.
    """
    suffix_len = max_length - len(prefix)
    suffix_chars = string.digits
    if not digit_only:
        suffix_chars += string.ascii_letters
    suffix = ''.join(random.choice(suffix_chars) for _ in range(suffix_len))
    return prefix + suffix


def get_alternative_type(device):
    """
    Attemt to detect the iface type through alternative means.
    """
    if os.path.exists(os.path.join(NET_PATH, device, 'device/physfn')):
        return Type.VF
    try:
        driver_name = ethtool.driver_name(device)
        iface_type = Type.NIC if driver_name else None
    except IOError as ioe:
        if ioe.errno == errno.EOPNOTSUPP:
            iface_type = Type.LOOPBACK if device == 'lo' else Type.DUMMY
        else:
            raise
    return iface_type


def _get_stat(device, stat_name):
    # From time to time, Linux returns an empty line, therefore, retry.
    TRIES = 5
    stat_path = '/sys/class/net/{}/statistics/{}'.format(device, stat_name)
    for attempt in reversed(range(TRIES)):
        try:
            with open(stat_path) as f:
                stat_val = f.read()
        except IOError as e:
            # silently ignore missing wifi stats
            if e.errno != errno.ENOENT:
                logging.debug('Could not read %s', stat_path, exc_info=True)
            return 0
        try:
            return int(stat_val)
        except ValueError:
            if stat_val != '':
                logging.warning(
                    'Could not parse stats (%s) from %s', stat_path, stat_val
                )
            logging.debug('bad %s: (%s)', stat_path, stat_val)
            if attempt == 0:
                raise
