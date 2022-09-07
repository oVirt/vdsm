# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import pytest

from collections import OrderedDict

from yajsonrpc.stomp import _heartbeat_frame as heartbeat_frame
from yajsonrpc.stomp import Command, Frame


# https://stomp.github.io/stomp-specification-1.2.html#Heart-beating
def test_heartbeat_frame():
    assert heartbeat_frame.encode() == b"\n"


# https://stomp.github.io/stomp-specification-1.2.html#Augmented_BNF
@pytest.mark.parametrize("command, expected", [
    (Command.CONNECT, b"CONNECT\n\n\x00"),
    (Command.SEND, b"SEND\n\n\x00"),
    (Command.SUBSCRIBE, b"SUBSCRIBE\n\n\x00"),
])
def test_encoding_frame_with_command_only(command, expected):
    assert Frame(command).encode() == expected


@pytest.mark.parametrize("headers, expected", [
    (
        {},
        b"CONNECT\n\n\x00"
    ),
    (
        {"abc": "def"},
        b"CONNECT\nabc:def\n\n\x00"
    ),
    (
        {"abc": "with\nnewline"},
        b"CONNECT\nabc:with\\nnewline\n\n\x00"
    ),
    (
        OrderedDict([("abc", "def"), ("xyz", "meh")]),
        b"CONNECT\nabc:def\nxyz:meh\n\n\x00"
    ),
])
def test_encoding_frame_with_headers(headers, expected):
    assert Frame(Command.CONNECT, headers).encode() == expected


@pytest.mark.parametrize("headers, payload, expected", [
    (
        {},
        "",
        b"SEND\ncontent-length:0\n\n\x00"
    ),
    (
        {},
        "zorro",
        b"SEND\ncontent-length:5\n\nzorro\x00"
    ),
    (
        {},
        b"zorro",
        b"SEND\ncontent-length:5\n\nzorro\x00"
    ),
    (
        {},
        u"\u0105b\u0107",
        b"SEND\ncontent-length:5\n\n\xc4\x85b\xc4\x87\x00"
    ),
    (
        OrderedDict([("abc", "def")]),
        "zorro",
        b"SEND\nabc:def\ncontent-length:5\n\nzorro\x00"
    ),
    (
        OrderedDict([("abc", "def")]),
        "with\x00null",
        b"SEND\nabc:def\ncontent-length:9\n\nwith\x00null\x00"
    ),
])
def test_encoding_frame_with_headers_and_payload(headers, payload, expected):
    assert Frame(Command.SEND, headers, payload).encode() == expected


def test_encoding_frame_should_fix_invalid_content_length():
    frame = Frame(Command.SEND, {"content-length": 3}, "6chars")
    assert frame.encode() == b"SEND\ncontent-length:6\n\n6chars\x00"


def test_frame_should_have_a_nice_repr():
    assert repr(Frame(Command.SEND)) == "<StompFrame command='SEND'>"


def test_frame_should_have_a_copy_method():
    original = Frame(Command.SEND, {"abc": "def"}, "zorro")
    original_encoded = original.encode()

    copy = original.copy()
    copy.command = Command.CONNECT
    copy.body = "batman"
    copy.headers["geh"] = "xyz"

    assert original.encode() == original_encoded
