# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import json

from stomp_test_utils import (
    FakeAsyncDispatcher,
    FakeConnection,
    FakeFrameHandler,
    FakeTimeGen
)
from yajsonrpc.stomp import (
    AsyncDispatcher,
    Command,
    Frame,
    Headers,
    DEFAULT_INTERVAL
)


def test_handle_connect():
    frame_handler = FakeFrameHandler()
    dispatcher = AsyncDispatcher(FakeConnection(), frame_handler)

    dispatcher.handle_connect(None)

    assert frame_handler.handle_connect_called


def test_handle_read():
    frame_handler = FakeFrameHandler()
    headers = {Headers.CONTENT_LENGTH: '78',
               Headers.DESTINATION: 'jms.topic.vdsm_responses',
               Headers.CONTENT_TYPE: 'application/json',
               Headers.SUBSCRIPTION: 'ad052acb-a934-4e10-8ec3-00c7417ef8d'}
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": "e8a936a6-d886-4cfa-97b9-2d54209053ff",
        "result": [],
    }).encode("utf-8")
    frame = Frame(command=Command.MESSAGE, headers=headers, body=body)
    dispatcher = AsyncDispatcher(FakeConnection(), frame_handler)
    dispatcher.handle_read(FakeAsyncDispatcher(None, data=frame.encode()))

    assert frame_handler.has_outgoing_messages
    recv_frame = frame_handler.pop_message()
    assert Command.MESSAGE == recv_frame.command
    assert body == recv_frame.body


def test_handle_error():
    frame_handler = FakeFrameHandler()
    connection = FakeConnection()
    dispatcher = AsyncDispatcher(connection, frame_handler)
    dispatcher.handle_error(dispatcher)
    assert connection.closed


def test_heartbeat_calc():
    dispatcher = AsyncDispatcher(
        FakeConnection(), FakeFrameHandler(),
        clock=FakeTimeGen([4000000.0, 4000002.0]).get_fake_time
    )
    dispatcher.setHeartBeat(8000, 0)

    assert 6 == dispatcher.next_check_interval()


def test_heartbeat_exceeded():
    frame_handler = FakeFrameHandler()
    dispatcher = AsyncDispatcher(
        FakeConnection(), frame_handler,
        clock=FakeTimeGen([4000000.0, 4000012.0]).get_fake_time
    )
    dispatcher.setHeartBeat(8000, 0)

    assert dispatcher.writable(None)
    assert frame_handler.has_outgoing_messages


def test_incoming_heartbeat_exceeded():
    frame_handler = FakeFrameHandler()
    connection = FakeConnection()
    dispatcher = AsyncDispatcher(
        connection, frame_handler,
        clock=FakeTimeGen(
            [4000000.0, 4000003.0, 4000006.0,
             4000009.0, 4000012.0]).get_fake_time)

    dispatcher.setHeartBeat(12000, 4000)
    assert not dispatcher.writable(None)
    assert not frame_handler.has_outgoing_messages


def test_no_heartbeat():
    dispatcher = AsyncDispatcher(FakeConnection(), FakeFrameHandler())
    dispatcher.setHeartBeat(0, 0)

    assert dispatcher.next_check_interval() == DEFAULT_INTERVAL


def test_no_outgoing_heartbeat():
    dispatcher = AsyncDispatcher(FakeConnection(), FakeFrameHandler())
    dispatcher = AsyncDispatcher(
        FakeConnection(), FakeFrameHandler(),
        clock=FakeTimeGen([4000000.0, 4000002.0, 4000004.0,
                           4000006.0]).get_fake_time
    )
    dispatcher.setHeartBeat(0, 8000)
    assert dispatcher.next_check_interval() == DEFAULT_INTERVAL


def test_handle_write():
    headers = {Headers.CONTENT_LENGTH: '78',
               Headers.DESTINATION: 'jms.topic.vdsm_responses',
               Headers.CONTENT_TYPE: 'application/json',
               Headers.SUBSCRIPTION: 'ad052acb-a934-4e10-8ec3-00c7417ef8d'}
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": "e8a936a6-d886-4cfa-97b9-2d54209053ff",
        "result": [],
    }).encode("utf-8")
    frame = Frame(command=Command.MESSAGE, headers=headers, body=body)
    frame_handler = FakeFrameHandler()
    frame_handler.handle_frame(None, frame)

    dispatcher = AsyncDispatcher(FakeConnection(), frame_handler)
    assert dispatcher.writable(None)

    dispatcher.handle_write(FakeAsyncDispatcher(''))
    assert not frame_handler.has_outgoing_messages


def test_handle_close():
    connection = FakeConnection()
    dispatcher = AsyncDispatcher(connection, FakeFrameHandler())

    dispatcher.handle_close(None)

    assert connection.closed
