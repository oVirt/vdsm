#
# Copyright 2012-2014 Red Hat, Inc.
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

from collections import namedtuple
from contextlib import closing
import ctypes
import fcntl
import socket

import ethtool

from vdsm.constants import EXT_TC
from vdsm.utils import execCmd

ERR_DEV_NOEXIST = 2

QDISC_INGRESS = 'ffff:'


class TrafficControlException(Exception):
    def __init__(self, errCode, message, command):
        self.errCode = errCode
        self.message = message
        self.command = command
        Exception.__init__(self, self.errCode, self.message, self.command)


def _addTarget(network, parent, target):
    fs = list(filters(network, parent))
    if fs:
        filt = fs[0]
    else:
        filt = Filter(prio=None, handle=None, actions=[])
    filt.actions.append(MirredAction(target))
    filter_replace(network, parent, filt)


def _delTarget(network, parent, target):
    fs = list(filters(network, parent))
    if fs:
        filt = fs[0]
    else:
        return []

    devices = set(ethtool.get_devices())
    acts = [act for act in filt.actions
            if act.target in devices and act.target != target]

    if acts:
        filt = Filter(prio=filt.prio, handle=filt.handle, actions=acts)
        filter_replace(network, parent, filt)
    else:
        filter_del(network, target, parent, filt.prio)
    return acts


def setPortMirroring(network, target):
    qdisc_replace_ingress(network)
    _addTarget(network, QDISC_INGRESS, target)

    qdisc_replace_prio(network)
    qdisc_id = _qdiscs_of_device(network).next()
    _addTarget(network, qdisc_id, target)

    set_promisc(network, True)


def unsetPortMirroring(network, target):
    # TODO handle the case where we have partial definitions on device due to
    # vdsm crash
    acts = _delTarget(network, QDISC_INGRESS, target)
    try:
        qdisc_id = _qdiscs_of_device(network).next()
        acts += _delTarget(network, qdisc_id, target)
    except StopIteration:
        pass

    if not acts:
        qdisc_del(network, 'root')
        qdisc_del(network, 'ingress')
        set_promisc(network, False)


def _process_request(command):
    retcode, out, err = execCmd(command, raw=True)
    if retcode != 0:
        raise TrafficControlException(retcode, err, command)
    return out


def qdisc_replace_ingress(dev):
    command = [EXT_TC, 'qdisc', 'add', 'dev', dev, 'ingress']
    try:
        _process_request(command)
    except TrafficControlException as e:
        if e.message == 'RTNETLINK answers: File exists\n':
            pass
        else:
            raise


def filter_del(dev, target, parent, prio):
    command = [EXT_TC, 'filter', 'del', 'dev', dev, 'parent', parent,
               'prio', prio]
    _process_request(command)


def filter_replace(dev, parent, filt):
    command = [EXT_TC, 'filter', 'replace', 'dev', dev, 'parent', parent]
    if filt.prio:
        command.extend(['prio', filt.prio])
        command.extend(['handle', filt.handle])
    command.extend(['protocol', 'ip', 'u32', 'match', 'u8', '0', '0'])
    for a in filt.actions:
        command.extend(['action', 'mirred', 'egress', 'mirror',
                        'dev', a.target])
    _process_request(command)


def qdisc_replace_prio(dev):
    command = [EXT_TC, 'qdisc', 'replace', 'dev', dev,
               'parent', 'root', 'prio']
    _process_request(command)


def _qdiscs_of_device(dev):
    "Return an iterator of qdisc_ids associated with dev"

    command = [EXT_TC, 'qdisc', 'show', 'dev', dev]
    out = _process_request(command)

    for line in out.splitlines():
        yield line.split(' ')[2]


def qdisc_del(dev, queue):
    try:
        command = [EXT_TC, 'qdisc', 'del', 'dev', dev, queue]
        _process_request(command)
    except TrafficControlException as e:
        if e.errCode != ERR_DEV_NOEXIST:
            raise


def set_flags(dev, flags):
    "Set device flags. We need this local definition until ethtool has it"

    SIOCSIFFLAGS = 0x8914

    class ifreq(ctypes.Structure):
        _fields_ = [("ifr_ifrn", ctypes.c_char * 16),
                    ("ifr_flags", ctypes.c_short)]

    with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as s:
        ifr = ifreq()
        ifr.ifr_ifrn = dev
        ifr.ifr_flags = flags

        fcntl.ioctl(s.fileno(), SIOCSIFFLAGS, ifr)


def set_promisc(dev, on=True):
    flags = ethtool.get_flags(dev)

    if bool(flags & ethtool.IFF_PROMISC) != on:
        if on:
            flags |= ethtool.IFF_PROMISC
        else:
            flags &= ~ethtool.IFF_PROMISC

        set_flags(dev, flags)


Filter = namedtuple('Filter', 'prio handle actions')
MirredAction = namedtuple('MirredAction', 'target')


def filters(dev, parent, out=None):
    """
    Return a (very) limitted information about tc filters on dev

    Function returns a generator of Filter objects.
    """

    if out is None:
        out = _process_request([EXT_TC, 'filter', 'show', 'dev', dev,
                               'parent', parent])

    HEADER = 'filter protocol ip pref '
    prio = handle = None
    actions = []
    prevline = ' '
    for line in out.splitlines() + [HEADER + 'X']:
        if line.startswith(HEADER):
            if prevline == ' ' and prio and handle and actions:
                yield Filter(prio, handle, actions)
                prio = handle = None
                actions = []
            else:
                elems = line.split()
                if len(elems) > 7:
                    prio = elems[4]
                    handle = elems[7]
        elif line.startswith('\taction order '):
            elems = line.split()
            if elems[3] == 'mirred' and elems[4] == '(Egress':
                actions.append(MirredAction(elems[-2][:-1]))
