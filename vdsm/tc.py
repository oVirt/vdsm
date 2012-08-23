#
# Copyright 2012 Red Hat, Inc.
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

import storage.misc
from vdsm.constants import EXT_TC, EXT_IFCONFIG

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
    filt = list(filters(network, parent))[0]
    filt.actions.remove(MirredAction(target))
    if filt.actions:
        filter_replace(network, parent, filt)
    else:
        filter_del(network, target, parent, filt.prio)
    return filt.actions


def setPortMirroring(network, target):
    qdisc_replace_ingress(network)
    _addTarget(network, QDISC_INGRESS, target)

    qdisc_replace_prio(network)
    qdisc_id = qdisc_get_devid(network)
    _addTarget(network, qdisc_id, target)

    set_promisc(network, True)


def unsetPortMirroring(network, target):
    # TODO handle the case where we have partial definitions on device due to
    # vdsm crash
    acts = _delTarget(network, QDISC_INGRESS, target)
    qdisc_id = qdisc_get_devid(network)
    acts += _delTarget(network, qdisc_id, target)

    if not acts:
        qdisc_del(network, 'root')
        qdisc_del(network, 'ingress')
        set_promisc(network, False)


def _process_request(command):
    retcode, out, err = storage.misc.execCmd(command, raw=True, sudo=False)
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

def qdisc_get_devid(dev):
    "Return qdisc_id of the first qdisc associated with dev"

    command = [EXT_TC, 'qdisc', 'show', 'dev', dev]
    out = _process_request(command)
    return out.split(' ')[2]

def qdisc_del(dev, queue):
    try:
        command = [EXT_TC, 'qdisc', 'del', 'dev', dev, queue]
        _process_request(command)
    except TrafficControlException, e:
        if e.errCode != ERR_DEV_NOEXIST:
            raise

def set_promisc(dev, on=True):
    promisc = 'promisc'
    if not on:
        promisc = '-promisc'
    command = [EXT_IFCONFIG, dev, promisc]
    _process_request(command)


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
