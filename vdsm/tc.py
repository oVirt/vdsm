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

from subprocess import list2cmdline

import storage.misc
from vdsm.constants import EXT_TC, EXT_IFCONFIG

ERR_DEV_NOEXIST = 2
PROC_ERROR_MSG = 'error executing command "%s" error: %s'

class TrafficControlException(Exception):
    def __init__(self, errCode, message):
        self.errCode = errCode
        self.message = message
        Exception.__init__(self, self.errCode, self.message)

def setPortMirroring(network, target):
    qdisc_add_ingress(network)
    add_filter(network, target, 'ffff:')
    qdisc_replace_parent(network)
    devid = qdisc_get_devid(network)
    add_filter(network, target, devid)
    set_promisc(network, True)

def unsetPortMirroring(network):
    qdisc_del(network, 'root')
    qdisc_del(network, 'ingress')
    set_promisc(network, False)

def _process_request(command):
    retcode, out, err = storage.misc.execCmd(command, raw=True, sudo=False)
    if retcode != 0:
        msg = PROC_ERROR_MSG % (list2cmdline(command), err)
        raise TrafficControlException(retcode, msg)
    return out

def qdisc_add_ingress(dev):
    command = [EXT_TC, 'qdisc', 'add', 'dev', dev, 'ingress']
    _process_request(command)

def add_filter(dev, target, parentId='ffff:'):
    command = [EXT_TC, 'filter', 'add', 'dev', dev, 'parent',
               parentId, 'protocol', 'ip', 'u32', 'match', 'u8', '0', '0',
               'action', 'mirred', 'egress', 'mirror', 'dev', target]
    _process_request(command)

def qdisc_replace_parent(dev):
    command = [EXT_TC, 'qdisc', 'replace', 'dev', dev,
               'parent', 'root', 'prio']
    _process_request(command)

def qdisc_get_devid(dev):
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
