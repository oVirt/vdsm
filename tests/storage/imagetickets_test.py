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

from __future__ import absolute_import
from __future__ import division

import json
import socket
import io

import six

from six.moves import http_client

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase
from testlib import expandPermutations, permutations
from testlib import recorded

from vdsm.common.units import GiB
from vdsm.storage import exception as se
from vdsm.storage import imagetickets


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


@expandPermutations
class TestImageTickets(VdsmTestCase):

    @MonkeyPatch(imagetickets, 'DAEMON_SOCK', "/no/such/path")
    @permutations([
        ["add_ticket", [{}]],
        ["extend_ticket", ["uuid", 300]],
        ["remove_ticket", ["uuid"]],
    ])
    def test_not_supported(self, method, args):
        with self.assertRaises(se.ImageDaemonUnsupported):
            func = getattr(imagetickets, method)
            func(*args)

    @MonkeyPatch(imagetickets, 'DAEMON_SOCK', __file__)
    @MonkeyPatch(imagetickets, 'UnixHTTPConnection', FakeUnixHTTPConnection())
    def test_add_ticket(self):
        ticket = create_ticket(uuid="uuid")
        body = json.dumps(ticket).encode("utf8")
        expected = [
            ("request", ("PUT", "/tickets/uuid"), {"body": body}),
        ]
        imagetickets.add_ticket(ticket)
        self.assertEqual(imagetickets.UnixHTTPConnection.__calls__, expected)
        self.assertTrue(imagetickets.UnixHTTPConnection.closed)

    @MonkeyPatch(imagetickets, 'DAEMON_SOCK', __file__)
    @MonkeyPatch(imagetickets, 'UnixHTTPConnection', FakeUnixHTTPConnection())
    def test_get_ticket(self):
        filename = u"\u05d0.raw"  # hebrew aleph
        ticket = create_ticket(uuid="uuid", filename=filename)
        data = json.dumps(ticket).encode("utf8")
        imagetickets.UnixHTTPConnection.response = FakeResponse(data=data)
        expected = [
            ("request", ("GET", "/tickets/uuid"), {"body": None}),
        ]
        result = imagetickets.get_ticket(ticket_id="uuid")
        self.assertEqual(result, ticket)
        self.assertEqual(imagetickets.UnixHTTPConnection.__calls__, expected)
        self.assertTrue(imagetickets.UnixHTTPConnection.closed)

    @MonkeyPatch(imagetickets, 'DAEMON_SOCK', __file__)
    @MonkeyPatch(imagetickets, 'UnixHTTPConnection', FakeUnixHTTPConnection())
    def test_extend_ticket(self):
        timeout = 300
        imagetickets.extend_ticket("uuid", timeout)
        body = '{"timeout": ' + str(timeout) + '}'
        expected = [
            ("request", ("PATCH", "/tickets/uuid"),
             {"body": body.encode("utf8")}),
        ]

        self.assertEqual(imagetickets.UnixHTTPConnection.__calls__, expected)
        self.assertTrue(imagetickets.UnixHTTPConnection.closed)

    @MonkeyPatch(imagetickets, 'DAEMON_SOCK', __file__)
    @MonkeyPatch(imagetickets, 'UnixHTTPConnection', FakeUnixHTTPConnection())
    def test_remove_ticket(self):
        # New imageio daemon will not return Content-Length header, as
        # specified in RFC 7230.
        imagetickets.UnixHTTPConnection.response = FakeResponse(
            status=204, reason="No Content", headers={})
        imagetickets.remove_ticket("uuid")
        expected = [
            ("request", ("DELETE", "/tickets/uuid"), {"body": None}),
        ]

        self.assertEqual(imagetickets.UnixHTTPConnection.__calls__, expected)
        self.assertTrue(imagetickets.UnixHTTPConnection.closed)

    @MonkeyPatch(imagetickets, 'DAEMON_SOCK', __file__)
    @MonkeyPatch(imagetickets, 'UnixHTTPConnection', FakeUnixHTTPConnection())
    def test_remove_ticket_with_content_length(self):
        # Legacy imageio daemon used to return "Content-Length: 0". This is not
        # correct according to RFC 7230, but we must support it.
        imagetickets.UnixHTTPConnection.response = FakeResponse(
            status=204, reason="No Content")
        imagetickets.remove_ticket("uuid")
        expected = [
            ("request", ("DELETE", "/tickets/uuid"), {"body": None}),
        ]

        self.assertEqual(imagetickets.UnixHTTPConnection.__calls__, expected)
        self.assertTrue(imagetickets.UnixHTTPConnection.closed)

    @MonkeyPatch(imagetickets, 'DAEMON_SOCK', __file__)
    @MonkeyPatch(imagetickets, 'UnixHTTPConnection', FakeUnixHTTPConnection())
    def test_res_header_error(self):
        imagetickets.UnixHTTPConnection.response = FakeResponse(
            status=300, headers={"content-length": "invalid"})
        with self.assertRaises(se.ImageDaemonError):
            imagetickets.remove_ticket("uuid")

    @MonkeyPatch(imagetickets, 'DAEMON_SOCK', __file__)
    @MonkeyPatch(imagetickets, 'UnixHTTPConnection', FakeUnixHTTPConnection())
    def test_res_invalid_json_ret(self):
        imagetickets.UnixHTTPConnection.response = FakeResponse(
            status=300, data=b"not a json string")
        with self.assertRaises(se.ImageDaemonError):
            imagetickets.remove_ticket("uuid")

    @MonkeyPatch(imagetickets, 'DAEMON_SOCK', __file__)
    @MonkeyPatch(imagetickets, 'UnixHTTPConnection', FakeUnixHTTPConnection())
    def test_image_daemon_error_ret(self):
        imagetickets.UnixHTTPConnection.response = FakeResponse(
            status=300, data=b'{"image_daemon_message":"content"}')
        try:
            imagetickets.remove_ticket("uuid")
        except se.ImageDaemonError as e:
            self.assertTrue(six.text_type("image_daemon_message") in e.value)
            self.assertTrue(six.text_type("content") in e.value)

    @MonkeyPatch(imagetickets, 'DAEMON_SOCK', __file__)
    @MonkeyPatch(imagetickets, 'UnixHTTPConnection', FakeUnixHTTPConnection())
    def test_res_read_error(self):
        imagetickets.UnixHTTPConnection.response = FakeResponse(
            status=300, data=b'{"image_daemon_message":"ignored"}')
        err_msg = "Environment error message"

        def read(amt=None):
            raise EnvironmentError(err_msg)

        imagetickets.UnixHTTPConnection.response.read = read

        try:
            imagetickets.remove_ticket("uuid")
        except se.ImageDaemonError as e:
            self.assertTrue(err_msg in e.value)

    @MonkeyPatch(imagetickets, 'DAEMON_SOCK', __file__)
    @MonkeyPatch(imagetickets, 'UnixHTTPConnection', FakeUnixHTTPConnection())
    @permutations([[http_client.HTTPException], [socket.error], [OSError]])
    def test_image_tickets_error(self, exc_type):
        ticket = create_ticket(uuid="uuid")

        def request(method, path, body=None):
            raise exc_type

        imagetickets.UnixHTTPConnection.request = request
        with self.assertRaises(se.ImageTicketsError):
            imagetickets.add_ticket(ticket)

    @MonkeyPatch(imagetickets, 'DAEMON_SOCK', __file__)
    @MonkeyPatch(imagetickets, 'UnixHTTPConnection', FakeUnixHTTPConnection())
    def test_request_with_response(self):
        ticket = create_ticket(uuid="uuid")
        data = json.dumps(ticket).encode("utf8")
        imagetickets.UnixHTTPConnection.response = FakeResponse(data=data)
        response = imagetickets.request("GET", "uuid")
        self.assertEqual(response, ticket)

    @MonkeyPatch(imagetickets, 'DAEMON_SOCK', __file__)
    @MonkeyPatch(imagetickets, 'UnixHTTPConnection', FakeUnixHTTPConnection())
    def test_request_with_empty_dict_response(self):
        response = imagetickets.request("DELETE", "uuid")
        self.assertEqual(response, {})


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
