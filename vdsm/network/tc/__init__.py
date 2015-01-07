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
from functools import partial
import errno

from vdsm import ipwrapper

from . import filter as tc_filter
from . import _parser
from . import cls
from . import qdisc
from ._wrapper import TrafficControlException

QDISC_INGRESS = 'ffff:'


def _addTarget(network, parent, target):
    fs = list(filters(network, parent))
    if fs:
        filt = fs[0]
    else:
        filt = Filter(prio=None, handle=None, actions=[])
    filt.actions.append(MirredAction(target))
    _filter_replace(network, parent, filt)


def _delTarget(network, parent, target):
    fs = list(filters(network, parent))
    if fs:
        filt = fs[0]
    else:
        return []

    devices = set(link.name for link in ipwrapper.getLinks())
    acts = [act for act in filt.actions
            if act.target in devices and act.target != target]

    if acts:
        filt = Filter(prio=filt.prio, handle=filt.handle, actions=acts)
        _filter_replace(network, parent, filt)
    else:
        tc_filter.delete(network, filt.prio, parent=parent)
    return acts


def setPortMirroring(network, target):
    _qdisc_replace_ingress(network)
    _addTarget(network, QDISC_INGRESS, target)

    qdisc.replace(network, 'prio', parent=None)
    qdisc_id = _qdiscs_of_device(network).next()
    _addTarget(network, qdisc_id, target)
    ipwrapper.getLink(network).promisc = True


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
        _qdisc_del(network)
        _qdisc_del(network, kind='ingress')
        ipwrapper.getLink(network).promisc = False


def _qdisc_replace_ingress(dev):
    try:
        qdisc.add(dev, 'ingress')
    except TrafficControlException as e:
        if e.errCode == errno.EEXIST:
            pass
        else:
            raise


def _filter_replace(dev, parent, filt):
    if filt.prio:
        kwargs = {'pref': filt.prio, 'handle': filt.handle}
    else:
        kwargs = {}
    actions = []
    for a in filt.actions:
        actions.append(['action', 'mirred', 'egress', 'mirror',
                        'dev', a.target])
    tc_filter.replace(dev, parent=parent, protocol='ip',
                      u32=['match', 'u8', '0', '0'], actions=actions, **kwargs)


def _qdiscs_of_device(dev):
    "Return an iterator of qdisc_ids associated with dev"
    for qdisc_data in _qdiscs(dev):
        yield qdisc_data['handle']


def _qdisc_del(*args, **kwargs):
    try:
        qdisc.delete(*args, **kwargs)
    except TrafficControlException as e:
        if e.errCode != errno.ENOENT:
            raise


Filter = namedtuple('Filter', 'prio handle actions')
MirredAction = namedtuple('MirredAction', 'target')


def filters(dev, parent, out=None):
    """
    Return a (very) limitted information about tc filters with mirred actions
    on dev.

    Function returns a generator of Filter objects.
    """
    for filt in _filters(dev, parent=parent, out=out):
        if 'u32' in filt and 'actions' in filt['u32']:
            yield Filter(
                filt['pref'], filt['u32']['fh'],
                [MirredAction(action['target']) for action in
                 filt['u32']['actions']])


def _iterate(module, dev, out=None, **kwargs):
    """
    Generates information dictionaries for a device or on a specific
    output.
    """
    if out is None:
        out = module.show(dev, **kwargs)

    for line in _parser.linearize(out.splitlines()):
        tokens = iter(line)
        _parser.consume(tokens, 'qdisc', 'class', 'filter')
        yield module.parse(tokens)


_filters = partial(_iterate, tc_filter)  # kwargs: parent and pref
_qdiscs = partial(_iterate, qdisc)  # kwargs: dev
classes = partial(_iterate, cls)  # kwargs: parent and classid
