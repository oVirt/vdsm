# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import functools
import json
import logging
import os
import socket

from contextlib import closing

import six
from six.moves import http_client

from vdsm.storage import exception as se

DAEMON_SOCK = "/run/ovirt-imageio/sock"

log = logging.getLogger('storage.imagetickets')


class UnixHTTPConnection(http_client.HTTPConnection):
    """
    HTTP connection over unix domain socket.
    """

    def __init__(self, path, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        self.path = path
        extra = {}
        if six.PY2:
            extra['strict'] = True
        http_client.HTTPConnection.__init__(
            self, "localhost", timeout=timeout, **extra)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if self.timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def requires_image_daemon(func):
    @functools.wraps(func)
    def wrapper(*args, **kw):
        if not os.path.exists(DAEMON_SOCK):
            raise se.ImageDaemonUnsupported()
        return func(*args, **kw)

    return wrapper


@requires_image_daemon
def add_ticket(ticket):
    body = json.dumps(ticket)
    _request("PUT", ticket["uuid"], body)


@requires_image_daemon
def get_ticket(ticket_id):
    response, content = _request("GET", ticket_id)
    try:
        return json.loads(content)
    except ValueError as e:
        error_info = {"explanation": "Invalid JSON", "detail": str(e)}
        raise se.ImageDaemonError(response.status, response.reason, error_info)


@requires_image_daemon
def extend_ticket(uuid, timeout):
    body = json.dumps({"timeout": timeout})
    _request("PATCH", uuid, body)


@requires_image_daemon
def remove_ticket(uuid):
    _request("DELETE", uuid)


def _request(method, uuid, body=None):
    log.debug("Sending request method=%r, ticket=%r, body=%r",
              method, uuid, body)
    if body is not None:
        body = body.encode("utf8")
    con = UnixHTTPConnection(DAEMON_SOCK)
    with closing(con):
        try:
            con.request(method, "/tickets/%s" % uuid, body=body)
            res = con.getresponse()
        except (http_client.HTTPException, EnvironmentError) as e:
            raise se.ImageTicketsError("Error communicating with "
                                       "ovirt-imageio-daemon: "
                                       "{error}".format(error=e))

        content = _read_content(res)
        if res.status >= 300:
            try:
                error = content.decode("utf8")
            except (UnicodeDecodeError, LookupError):
                error = repr(content)
            raise se.ImageDaemonError(res.status, res.reason, error)
        return res, content


def _read_content(response):
    # We must consume the entire response, otherwise we cannot use keep-alive
    # connections. HTTPResponse.read() is doing the right thing, handling
    # request content length or chunked encoding.
    try:
        # This can also be a "200 OK" with "Content-Length: 0",
        # or "204 No Content" without Content-Length header.
        # See https://tools.ietf.org/html/rfc7230#section-3.3.2
        return response.read()
    except EnvironmentError as e:
        error_info = {"explanation": "Error reading response",
                      "detail": str(e)}
        raise se.ImageDaemonError(response.status, response.reason, error_info)
