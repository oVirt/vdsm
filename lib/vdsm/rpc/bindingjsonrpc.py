# Copyright (C) 2012 Adam Litke, IBM Corporation
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import functools
import logging

from yajsonrpc import JsonRpcServer
from yajsonrpc.stompserver import StompReactor

from vdsm import executor
from vdsm.common import concurrent
from vdsm.config import config


# TODO test what should be the default values
_TIMEOUT = config.getint('rpc', 'worker_timeout')
_THREADS = config.getint('rpc', 'worker_threads')
_TASK_PER_WORKER = config.getint('rpc', 'tasks_per_worker')
_TASKS = _THREADS * _TASK_PER_WORKER


class BindingJsonRpc(object):
    log = logging.getLogger('BindingJsonRpc')

    def __init__(self, bridge, subs, timeout, scheduler, cif):
        self._executor = executor.Executor(name="jsonrpc",
                                           workers_count=_THREADS,
                                           max_tasks=_TASKS,
                                           scheduler=scheduler)
        self._bridge = bridge
        self._server = JsonRpcServer(
            bridge, timeout, cif,
            functools.partial(self._executor.dispatch,
                              timeout=_TIMEOUT, discard=False))
        self._reactor = StompReactor(subs)
        self.startReactor()

    def add_socket(self, reactor, client_socket):
        reactor.createListener(client_socket, self._onAccept)

    def _onAccept(self, client):
        client.set_message_handler(self._server.queueRequest)

    @property
    def reactor(self):
        return self._reactor

    @property
    def bridge(self):
        return self._bridge

    def start(self):
        self._executor.start()

        t = concurrent.thread(self._server.serve_requests,
                              name='JsonRpcServer')
        t.start()

    def startReactor(self):
        reactorName = self._reactor.__class__.__name__
        t = concurrent.thread(self._reactor.process_requests,
                              name='JsonRpc (%s)' % reactorName)
        t.start()

    def stop(self):
        self._server.stop()
        self._reactor.stop()
        self._executor.stop()
