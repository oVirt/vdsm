# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import itertools

from collections import deque


class FakeAsyncClient(object):
    def __init__(self):
        self._queue = []

    def pop_message(self):
        return self._queue.pop(0)

    def empty(self):
        return len(self._queue) == 0

    def queue_frame(self, msg):
        self._queue.append(msg)


class FakeConnection(object):

    def __init__(self, client=None):
        self._client = client
        self._flow_id = None
        self.closed = False

    def send_raw(self, msg):
        self._client.queue_frame(msg)

    def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed

    @property
    def flow_id(self):
        return self._flow_id

    def handleMessage(self, data, flow_id):
        self._flow_id = flow_id
        self._client.queue_frame(data)

    def set_heartbeat(self, out_interval, in_interval):
        pass


class FakeAsyncDispatcher(object):

    socket = None

    def __init__(self, client, data=None):
        self._client = client
        self._connection = FakeConnection(self._client)
        self._data = data

    def recv(self, buffer_size):
        return self._data

    def send(self, data):
        return len(data)

    def setHeartBeat(self, outgoing, incoming=0):
        pass

    @property
    def connection(self):
        return self._connection

    def handle_timeout(self):
        pass


class FakeSubscription(object):

    def __init__(self, destination, id):
        self._destination = destination
        self._id = id

    def set_client(self, client):
        self._client = FakeConnection(client)

    @property
    def destination(self):
        return self._destination

    @property
    def id(self):
        return self._id

    @property
    def client(self):
        return self._client


class FakeFrameHandler(object):

    def __init__(self):
        self.handle_connect_called = False
        self._outbox = deque()

    def handle_connect(self):
        self.handle_connect_called = True

    def handle_frame(self, dispatcher, frame):
        self.queue_frame(frame)

    def handle_timeout(self, dispatcher):
        dispatcher.connection.close()

    def handle_error(self, dispatcher):
        self.handle_timeout(dispatcher)

    def peek_message(self):
        return self._outbox[0]

    def pop_message(self):
        return self._outbox.popleft()

    @property
    def has_outgoing_messages(self):
        return (len(self._outbox) > 0)

    def queue_frame(self, frame):
        self._outbox.append(frame)

    def handle_close(self, dispatcher):
        dispatcher.connection.close()


class FakeTimeGen(object):

    def __init__(self, list):
        self._chain = itertools.chain(list)

    def get_fake_time(self):
        next_time = next(self._chain)
        print(next_time)
        return next_time
