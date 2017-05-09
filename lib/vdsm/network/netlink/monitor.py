# Copyright 2014-2017 Red Hat, Inc.
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
from contextlib import closing, contextmanager
from ctypes import CFUNCTYPE, c_int, c_void_p, py_object
from six.moves import queue
import logging
import os
import select
import threading

from vdsm.utils import NoIntrPoll
from vdsm import concurrent
from vdsm.common.osutils import uninterruptible
from vdsm.common.time import monotonic_time

from . import (LIBNL, _GROUPS, _NL_ROUTE_ADDR_NAME, _NL_ROUTE_LINK_NAME,
               _NL_ROUTE_NAME, _NL_STOP, _add_socket_memberships,
               _close_socket, _drop_socket_memberships, _int_proto,
               _nl_msg_parse, _nl_object_get_type, _nl_recvmsgs_default,
               _nl_socket_get_fd, _open_socket)
from .addr import _addr_info
from .link import _link_info
from .route import _route_info

# If monitoring thread is running, queue waiting for new value and we call
# stop(), we have to stop queue by passing special code.
_STOP_FLAG = 31
_TIMEOUT_FLAG = 32

E_NOT_RUNNING = 1
E_TIMEOUT = 2


class MonitorError(Exception):
    pass


class Monitor(object):
    """Netlink monitor. Usage:

    Get events collected while the monitor was running:
    mon = Monitor()
    mon.start()
    ...
    mon.stop()
    for event in mon:
        handle event
    mon.wait()

    Monitoring events synchronously:
    mon = Monitor()
    mon.start()
    for event in mon:
        if foo:
            mon.stop()
        handle event
    mon.wait()

    Monitoring events with defined timeout. If timeout expires during
    iteration and silent_timeout is set to False, MonitorError(E_TIMEOUT) is
    raised by iteration:
    mon = Monitor(timeout=2)
    mon.start()
    for event in mon:
        handle event
    mon.wait()

    Monitor defined groups (monitor everything if not set):
    mon = Monitor(groups=('link', 'ipv4-route'))
    mon.start()
    for event in mon:
        if foo:
            mon.stop()
        handle event
    mon.wait()

    Possible groups: link, notify, neigh, tc, ipv4-ifaddr, ipv4-mroute,
    ipv4-route, ipv6-ifaddr, ipv6-mroute, ipv6-route, ipv6-ifinfo,
    decnet-ifaddr, decnet-route, ipv6-prefix
    """
    def __init__(self, groups=frozenset(), timeout=None, silent_timeout=False):
        self._time_start = None
        self._timeout = timeout
        self._silent_timeout = silent_timeout
        if groups:
            unknown_groups = frozenset(groups).difference(frozenset(_GROUPS))
            if unknown_groups:
                raise AttributeError('Invalid groups: %s' % (unknown_groups,))
            self._groups = groups
        else:
            self._groups = _GROUPS.keys()
        self._queue = queue.Queue()
        self._scan_thread = concurrent.thread(self._scan,
                                              name="netlink/events")
        self._scanning_started = threading.Event()
        self._scanning_stopped = threading.Event()

    def __iter__(self):
        for event in iter(self._queue.get, None):
            if event == _TIMEOUT_FLAG:
                if self._silent_timeout:
                    break
                raise MonitorError(E_TIMEOUT)
            elif event == _STOP_FLAG:
                break
            yield event

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        if not self.is_stopped():
            self.stop()
        self.wait()

    def start(self):
        if self._timeout:
            self._end_time = monotonic_time() + self._timeout
        self._scan_thread.start()
        self._scanning_started.wait()

    def _scan(self):
        with closing(select.epoll()) as epoll:
            with _monitoring_socket(self._queue, self._groups, epoll) as sock:
                with _pipetrick(epoll) as self._pipetrick:
                    self._scanning_started.set()
                    while True:
                        if self._timeout:
                            timeout = self._end_time - monotonic_time()
                            # timeout expired
                            if timeout <= 0:
                                self._scanning_stopped.set()
                                self._queue.put(_TIMEOUT_FLAG)
                                break
                        else:
                            timeout = -1

                        events = NoIntrPoll(epoll.poll, timeout=timeout)
                        # poll timeouted
                        if len(events) == 0:
                            self._scanning_stopped.set()
                            self._queue.put(_TIMEOUT_FLAG)
                            break
                        # stopped by pipetrick
                        elif (self._pipetrick[0], select.POLLIN) in events:
                            uninterruptible(os.read, self._pipetrick[0], 1)
                            self._queue.put(_STOP_FLAG)
                            break

                        _nl_recvmsgs_default(sock)

    def stop(self):
        if self.is_stopped():
            raise MonitorError(E_NOT_RUNNING)
        else:
            self._scanning_stopped.set()
            self._scanning_started.wait()
            os.write(self._pipetrick[1], b'c')

    def is_stopped(self):
        return self._scanning_stopped.is_set()

    def wait(self):
        self._scan_thread.join()


# libnl/include/linux/rtnetlink.h
_EVENTS = {
    16: 'new_link',            # RTM_NEWLINK
    17: 'del_link',            # RTM_DELLINK
    18: 'get_link',            # RTM_GETLINK
    19: 'set_link',            # RTM_SETLINK
    20: 'new_addr',            # RTM_NEWADDR
    21: 'del_addr',            # RTM_DELADDR
    22: 'get_addr',            # RTM_GETADDR
    24: 'new_route',           # RTM_NEWROUTE
    25: 'del_route',           # RTM_DELROUTE
    26: 'get_route',           # RTM_GETROUTE
    28: 'new_neigh',           # RTM_NEWNEIGH
    29: 'del_neigh',           # RTM_DELNEIGH
    30: 'get_neigh',           # RTM_GETNEIGH
    32: 'new_rule',            # RTM_NEWRULE
    33: 'del_rule',            # RTM_DELRULE
    34: 'get_rule',            # RTM_GETRULE
    36: 'new_qdisc',           # RTM_NEWQDISC
    37: 'del_qdisc',           # RTM_DELQDISC
    38: 'get_qdisc',           # RTM_GETQDISC
    40: 'new_tclass',          # RTM_NEWTCLASS
    41: 'del_tclass',          # RTM_DELTCLASS
    42: 'get_tclass',          # RTM_GETTCLASS
    44: 'new_tfilter',         # RTM_NEWTFILTER
    45: 'del_tfilter',         # RTM_DELTFILTER
    46: 'get_tfilter',         # RTM_GETTFILTER
    48: 'new_action',          # RTM_NEWACTION
    49: 'del_action',          # RTM_DELACTION
    50: 'get_action',          # RTM_GETACTION
    52: 'new_prefix',          # RTM_NEWPREFIX
    58: 'get_multicast',       # RTM_GETMULTICAST
    62: 'get_anycast',         # RTM_GETANYCAST
    64: 'new_neightbl',        # RTM_NEWNEIGHTBL
    66: 'get_neightbl',        # RTM_GETNEIGHTBL
    67: 'set_neightbl',        # RTM_SETNEIGHTBL
    68: 'new_nduseropt',       # RTM_NEWNDUSEROPT
    72: 'new_addrlabel',       # RTM_NEWADDRLABEL
    73: 'del_addrlabel',       # RTM_DELADDRLABEL
    74: 'get_addrlabel',       # RTM_GETADDRLABEL
    78: 'get_dcb',             # RTM_GETDCB
    79: 'set_dcb'}             # RTM_SETDCB


def _object_input(obj, queue):
    """This function serves as a callback for nl_msg_parse(message, callback,
    extra_argument) function. When nl_msg_parse() is called, it passes message
    as an object to defined callback with optional extra argument (monitor's
    queue in our case)
    """
    obj_type = _nl_object_get_type(obj)
    obj_dict = None
    if obj_type == _NL_ROUTE_ADDR_NAME:
        obj_dict = _addr_info(obj)
    elif obj_type == _NL_ROUTE_LINK_NAME:
        obj_dict = _link_info(obj)
    elif obj_type.split('/', 1)[0] == _NL_ROUTE_NAME:
        obj_dict = _route_info(obj)

    if obj_dict is not None:
        msg_type = _nl_object_get_msgtype(obj)
        try:
            obj_dict['event'] = _EVENTS[msg_type]
        except KeyError:
            logging.error('unexpected msg_type %s', msg_type)
        else:
            queue.put(obj_dict)
_c_object_input = CFUNCTYPE(c_void_p, c_void_p, py_object)(_object_input)


def _event_input(msg, queue):
    """This function serves as a callback for netlink socket. When socket
    recieves a message, it passes it to callback function with optional extra
    argument (monitor's queue in this case)
    """
    nl_error = _nl_msg_parse(msg, _c_object_input, queue)
    if nl_error < 0:
        logging.error('EventMonitor nl_msg_parse() failed with %d', nl_error)
    return _NL_STOP
_c_event_input = CFUNCTYPE(c_int, c_void_p, c_void_p)(_event_input)


@contextmanager
def _monitoring_socket(queue, groups, epoll):
    c_queue = py_object(queue)
    sock = _open_socket(callback_function=_c_event_input, callback_arg=c_queue)
    try:
        _add_socket_memberships(sock, groups)
        try:
            fd = _nl_socket_get_fd(sock)
            epoll.register(fd, select.EPOLLIN)
            try:
                yield sock
            finally:
                epoll.unregister(fd)
        finally:
            _drop_socket_memberships(sock, groups)
    finally:
        _close_socket(sock)


@contextmanager
def _pipetrick(epoll):
    pipetrick = os.pipe()
    try:
        epoll.register(pipetrick[0], select.EPOLLIN)
        try:
            yield pipetrick
        finally:
            epoll.unregister(pipetrick[0])
    finally:
        os.close(pipetrick[0])
        os.close(pipetrick[1])

_nl_object_get_msgtype = _int_proto(('nl_object_get_msgtype', LIBNL))
