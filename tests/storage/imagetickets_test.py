#
# Copyright 2016 Red Hat, Inc.
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

import http.client
import json
import socket
import io

import pytest

from vdsm.common.units import GiB
from vdsm.storage import exception as se
from vdsm.storage import imagetickets

from testlib import recorded


class FakeResponse(object):

    def __init__(self, status=200, reason="OK", headers=None, data=b""):
        self.status = status
        self.reason = reason
        if headers is None:
            headers = {"content-length": str(len(data))}
        self.headers = headers
        self.file = io.BytesIO(data)

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, amt=None):
        return self.file.read(amt)


class FakeUnixHTTPConnection(object):

    def __init__(self, response=None):
        self.path = None
        self.timeout = None
        self.closed = False
        self.response = response or FakeResponse()

    def __call__(self, path, timeout=None):
        self.path = path
        self.timeout = timeout
        return self

    @recorded
    def request(self, method, path, body=None):
        pass

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


@pytest.fixture
def invalid_socket(monkeypatch):
    monkeypatch.setattr(imagetickets, "DAEMON_SOCK", "/no/such/path")


@pytest.fixture
def fake_connection(monkeypatch):
    monkeypatch.setattr(imagetickets, "DAEMON_SOCK", __file__)
    monkeypatch.setattr(
        imagetickets, "UnixHTTPConnection", FakeUnixHTTPConnection())


@pytest.mark.parametrize("method, args", [
    ["add_ticket", [{}]],
    ["extend_ticket", ["uuid", 300]],
    ["remove_ticket", ["uuid"]],
])
def test_not_supported(invalid_socket, method, args):
    with pytest.raises(se.ImageDaemonUnsupported):
        func = getattr(imagetickets, method)
        func(*args)


def test_add_ticket(fake_connection):
    ticket = create_ticket(uuid="uuid")
    body = json.dumps(ticket).encode("utf8")
    expected = [
        ("request", ("PUT", "/tickets/uuid"), {"body": body}),
    ]
    imagetickets.add_ticket(ticket)
    assert imagetickets.UnixHTTPConnection.__calls__ == expected
    assert imagetickets.UnixHTTPConnection.closed


def test_get_ticket(fake_connection):
    filename = u"\u05d0.raw"  # hebrew aleph
    ticket = create_ticket(uuid="uuid", filename=filename)
    data = json.dumps(ticket).encode("utf8")
    imagetickets.UnixHTTPConnection.response = FakeResponse(data=data)
    expected = [
        ("request", ("GET", "/tickets/uuid"), {"body": None}),
    ]
    result = imagetickets.get_ticket(ticket_id="uuid")
    assert result == ticket
    assert imagetickets.UnixHTTPConnection.__calls__ == expected
    assert imagetickets.UnixHTTPConnection.closed


def test_extend_ticket(fake_connection):
    timeout = 300
    imagetickets.extend_ticket("uuid", timeout)
    body = '{"timeout": ' + str(timeout) + '}'
    expected = [
        ("request", ("PATCH", "/tickets/uuid"),
         {"body": body.encode("utf8")}),
    ]

    assert imagetickets.UnixHTTPConnection.__calls__ == expected
    assert imagetickets.UnixHTTPConnection.closed


def test_remove_ticket(fake_connection):
    # New imageio daemon will not return Content-Length header, as
    # specified in RFC 7230.
    imagetickets.UnixHTTPConnection.response = FakeResponse(
        status=204, reason="No Content", headers={})
    imagetickets.remove_ticket("uuid")
    expected = [
        ("request", ("DELETE", "/tickets/uuid"), {"body": None}),
    ]

    assert imagetickets.UnixHTTPConnection.__calls__ == expected
    assert imagetickets.UnixHTTPConnection.closed


def test_remove_ticket_with_content_length(fake_connection):
    # Legacy imageio daemon used to return "Content-Length: 0". This is not
    # correct according to RFC 7230, but we must support it.
    imagetickets.UnixHTTPConnection.response = FakeResponse(
        status=204, reason="No Content")
    imagetickets.remove_ticket("uuid")
    expected = [
        ("request", ("DELETE", "/tickets/uuid"), {"body": None}),
    ]

    assert imagetickets.UnixHTTPConnection.__calls__ == expected
    assert imagetickets.UnixHTTPConnection.closed


def test_res_header_error(fake_connection):
    imagetickets.UnixHTTPConnection.response = FakeResponse(
        status=300, headers={"content-length": "invalid"})
    with pytest.raises(se.ImageDaemonError):
        imagetickets.remove_ticket("uuid")


def test_res_invalid_json_ret(fake_connection):
    imagetickets.UnixHTTPConnection.response = FakeResponse(
        status=300, data=b"not a json string")
    with pytest.raises(se.ImageDaemonError):
        imagetickets.remove_ticket("uuid")


def test_image_daemon_error_ret(fake_connection):
    imagetickets.UnixHTTPConnection.response = FakeResponse(
        status=300, data=b'{"image_daemon_message":"content"}')
    try:
        imagetickets.remove_ticket("uuid")
    except se.ImageDaemonError as e:
        assert "image_daemon_message" in e.value
        assert "content" in e.value


def test_res_read_error(fake_connection):
    imagetickets.UnixHTTPConnection.response = FakeResponse(
        status=300, data=b'{"image_daemon_message":"ignored"}')
    err_msg = "Environment error message"

    def read(amt=None):
        raise EnvironmentError(err_msg)

    imagetickets.UnixHTTPConnection.response.read = read

    with pytest.raises(se.ImageDaemonError) as e:
        imagetickets.remove_ticket("uuid")
        assert err_msg in e.value


@pytest.mark.parametrize("exc_type", [
    http.client.HTTPException, socket.error, OSError
])
def test_image_tickets_error(fake_connection, exc_type):
    ticket = create_ticket(uuid="uuid")

    def request(method, path, body=None):
        raise exc_type

    imagetickets.UnixHTTPConnection.request = request
    with pytest.raises(se.ImageTicketsError):
        imagetickets.add_ticket(ticket)


def test_request_with_response(fake_connection):
    ticket = create_ticket(uuid="uuid")
    data = json.dumps(ticket).encode("utf8")
    imagetickets.UnixHTTPConnection.response = FakeResponse(data=data)
    response = imagetickets.request("GET", "uuid")
    assert response == ticket


def test_request_with_empty_dict_response(fake_connection):
    response = imagetickets.request("DELETE", "uuid")
    assert response == {}


def create_ticket(uuid, ops=("read", "write"), timeout=300,
                  size=GiB, path="/path/to/image", filename=None):
    ticket = {
        "uuid": uuid,
        "timeout": timeout,
        "ops": list(ops),
        "size": size,
        "path": path,
    }
    if filename is not None:
        ticket["filename"] = filename
    return ticket
