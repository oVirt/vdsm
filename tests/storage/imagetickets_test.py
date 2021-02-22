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

    def __init__(self, status=200, reason="OK", data=b""):
        self.status = status
        self.reason = reason
        self.headers = {}

        # For 204 "No content", imageio daemon does not return Content-Length
        # header, as specified in RFC 7230.
        if status != 204:
            self.headers["content-length"] = str(len(data))
            self.headers["content-type"] = "text/plain; charset=UTF-8"

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
    con = FakeUnixHTTPConnection()
    monkeypatch.setattr(imagetickets, "UnixHTTPConnection", con)
    return con


def test_remove_ticket_error(fake_connection):
    fake_connection.response = FakeResponse(status=409, data=b'Conflict')
    with pytest.raises(se.ImageDaemonError) as e:
        imagetickets.remove_ticket("uuid")
    assert "Conflict" in str(e.value)


def test_extend_ticket_error(fake_connection):
    fake_connection.response = FakeResponse(status=404, data=b'Not found')
    with pytest.raises(se.ImageDaemonError) as e:
        imagetickets.extend_ticket("uuid", 1)
    assert "Not found" in str(e.value)


def test_get_ticket_error(fake_connection):
    fake_connection.response = FakeResponse(status=404, data=b'Not found')
    with pytest.raises(se.ImageDaemonError) as e:
        imagetickets.get_ticket("uuid")
    assert "Not found" in str(e.value)


@pytest.mark.parametrize("content_type", [
    "text/plain",
    "text/plain; unknown=unknown",
    "text/plain; charset=unknown",
])
def test_parse_text_plain_charset(fake_connection, content_type):
    fake_response = FakeResponse(status=404, data=b'Not found error')
    fake_response.headers["content-type"] = content_type
    fake_connection.response = fake_response

    with pytest.raises(se.ImageDaemonError) as e:
        imagetickets.get_ticket("uuid")
    assert "Not found error" in str(e.value)


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
    assert fake_connection.__calls__ == expected
    assert fake_connection.closed


def test_get_ticket(fake_connection):
    filename = u"\u05d0.raw"  # hebrew aleph
    ticket = create_ticket(uuid="uuid", filename=filename)
    data = json.dumps(ticket).encode("utf8")
    fake_response = FakeResponse(data=data)
    fake_response.headers["content-type"] = "application/json"
    fake_connection.response = fake_response
    expected = [
        ("request", ("GET", "/tickets/uuid"), {"body": None}),
    ]
    result = imagetickets.get_ticket(ticket_id="uuid")
    assert result == ticket
    assert fake_connection.__calls__ == expected
    assert fake_connection.closed


def test_extend_ticket(fake_connection):
    timeout = 300
    imagetickets.extend_ticket("uuid", timeout)
    body = '{"timeout": ' + str(timeout) + '}'
    expected = [
        ("request", ("PATCH", "/tickets/uuid"),
         {"body": body.encode("utf8")}),
    ]

    assert fake_connection.__calls__ == expected
    assert fake_connection.closed


def test_remove_ticket(fake_connection):
    fake_connection.response = FakeResponse(status=204, reason="No Content")
    imagetickets.remove_ticket("uuid")
    expected = [
        ("request", ("DELETE", "/tickets/uuid"), {"body": None}),
    ]

    assert fake_connection.__calls__ == expected
    assert fake_connection.closed


def test_res_header_error(fake_connection):
    fake_response = FakeResponse(status=300)
    fake_response.headers["content-length"] = "invalid"
    fake_connection.response = fake_response

    with pytest.raises(se.ImageDaemonError):
        imagetickets.remove_ticket("uuid")


def test_res_read_error(fake_connection):
    fake_connection.response = FakeResponse(status=300)
    err_msg = "Environment error message"

    def read(amt=None):
        raise EnvironmentError(err_msg)

    fake_connection.response.read = read

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

    fake_connection.request = request
    with pytest.raises(se.ImageTicketsError):
        imagetickets.add_ticket(ticket)


def test_request_with_response(fake_connection):
    ticket = create_ticket(uuid="uuid")
    data = json.dumps(ticket).encode("utf8")
    fake_connection.response = FakeResponse(data=data)
    response = imagetickets.get_ticket("uuid")
    assert response == ticket


def test_request_with_zero_content_length(fake_connection):
    fake_connection.response = FakeResponse()
    with pytest.raises(se.ImageDaemonError):
        imagetickets.get_ticket("uuid")


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
