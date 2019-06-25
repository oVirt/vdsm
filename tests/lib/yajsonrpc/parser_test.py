#
# Copyright 2019 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

import pytest
import six

from yajsonrpc.stomp import Command, Frame, Parser


def test_empty_parser():
    parser = Parser()
    assert parser.pending == 0
    assert parser.popFrame() is None


@pytest.mark.parametrize("command", [
    Command.CONNECT, Command.SEND, Command.DISCONNECT
])
@pytest.mark.skipif(six.PY3, reason="needs porting to py3")
def test_parsing_simple_frame(command):
    parser = Parser()
    parser.parse(Frame(command).encode())
    parsed_frame = parser.popFrame()

    assert parsed_frame.command == command
    assert parsed_frame.headers == {}
    assert parsed_frame.body == b""


@pytest.mark.parametrize("headers", [
    {},
    {"abc": "def"},
    {"abc": "def", "geh": "xyz"},
    {u"\u0105b\u0107": "def"},
    {"abc": "with\nescaped:chars"},
])
@pytest.mark.skipif(six.PY3, reason="needs porting to py3")
def test_parsing_frame_with_headers(headers):
    parser = Parser()
    frame = Frame(Command.CONNECT, headers)
    parser.parse(frame.encode())
    parsed_frame = parser.popFrame()

    assert parsed_frame.command == Command.CONNECT
    assert parsed_frame.headers == headers
    assert parsed_frame.body == b""


@pytest.mark.parametrize("body", [
    b"zorro",
    u"\u0105b\u0107".encode("utf-8")
])
@pytest.mark.skipif(six.PY3, reason="needs porting to py3")
def test_parsing_frame_with_headers_and_body(body):
    parser = Parser()
    frame = Frame(Command.CONNECT, {"abc": "def"}, body)
    parser.parse(frame.encode())
    parsed_frame = parser.popFrame()

    assert parsed_frame.command == Command.CONNECT
    assert "abc" in parsed_frame.headers
    assert parsed_frame.headers["abc"] == "def"
    assert "content-length" in parsed_frame.headers
    assert int(parsed_frame.headers["content-length"]) == len(body)
    assert parsed_frame.body == body


@pytest.mark.skipif(six.PY3, reason="needs porting to py3")
def test_parser_should_accept_frames_with_crlf_eols():
    parser = Parser()
    frame = Frame(Command.CONNECT, {"abc": "def"}, b"zorro")
    encoded_frame = frame.encode().replace(b"\n", b"\r\n")
    parser.parse(encoded_frame)
    parsed_frame = parser.popFrame()

    assert parsed_frame.command == Command.CONNECT
    assert "abc" in parsed_frame.headers
    assert parsed_frame.headers["abc"] == "def"
    assert "content-length" in parsed_frame.headers
    assert int(parsed_frame.headers["content-length"]) == 5
    assert parsed_frame.body == b"zorro"


@pytest.mark.skipif(six.PY3, reason="needs porting to py3")
def test_parser_should_handle_frames_with_no_content_length():
    encoded_frame = b"CONNECT\nabc:def\n\nzorro\x00"
    parser = Parser()

    parser.parse(encoded_frame)
    parsed_frame = parser.popFrame()

    assert parsed_frame.command == Command.CONNECT
    assert parsed_frame.headers == {"abc": "def"}
    assert parsed_frame.body == b"zorro"


@pytest.mark.skipif(six.PY3, reason="needs porting to py3")
def test_parser_should_raise_for_frames_with_invalid_content_length():
    encoded_frame = b"CONNECT\nabc:def\ncontent-length:3\n\n6chars\x00"
    parser = Parser()

    with pytest.raises(RuntimeError) as err:
        parser.parse(encoded_frame)

    assert "Frame end is missing \\0" in str(err.value)


@pytest.mark.parametrize("encoded_frame", [
    b"CONNECT\nabc:def\ncontent-length:5\n\nzorro\x00",
    b"CONNECT\nabc:def\n\nzorro\x00",
])
@pytest.mark.skipif(six.PY3, reason="needs porting to py3")
def test_parser_should_wait_until_frame_is_fully_transfered(encoded_frame):
    parser = Parser()

    # When iterating over bytes in py3 you get ints, not byte slices,
    # so we need to use this quirky way of obtaining single-byte slices
    single_bytes = [encoded_frame[i:i + 1] for i in range(len(encoded_frame))]

    for byte in single_bytes[:-1]:
        parser.parse(byte)
        assert parser.pending == 0
        assert parser.popFrame() is None

    parser.parse(single_bytes[-1])
    assert parser.pending == 1

    frame = parser.popFrame()
    assert frame is not None
    assert frame.command == Command.CONNECT
    assert frame.body == b"zorro"


@pytest.mark.skipif(six.PY3, reason="needs porting to py3")
def test_parser_should_skip_heartbeat_frames():
    parser = Parser()
    heartbeats = b"\n\n\n\n\n"
    encoded_frame = Frame(Command.CONNECT).encode()

    parser.parse(heartbeats + encoded_frame)
    assert parser.pending == 1

    decoded_frame = parser.popFrame()
    assert decoded_frame is not None
    assert decoded_frame.command == Command.CONNECT
