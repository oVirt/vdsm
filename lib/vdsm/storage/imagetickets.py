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

import functools
import json
import logging
import os

from contextlib import closing
from six.moves import http_client

try:
    from ovirt_imageio_daemon import uhttp
except ImportError:
    uhttp = None

from vdsm import constants
from vdsm.storage import exception as se

DAEMON_SOCK = os.path.join(constants.P_VDSM_RUN, "ovirt-imageio-daemon.sock")

log = logging.getLogger('storage.imagetickets')


def requires_image_daemon(func):
    @functools.wraps(func)
    def wrapper(*args, **kw):
        if not uhttp:
            raise se.ImageDaemonUnsupported()
        return func(*args, **kw)

    return wrapper


@requires_image_daemon
def add_ticket(ticket):
    body = json.dumps(ticket)
    request(uhttp.PUT, ticket["uuid"], body)


@requires_image_daemon
def extend_ticket(uuid, timeout):
    body = json.dumps({"timeout": timeout})
    request(uhttp.PATCH, uuid, body)


@requires_image_daemon
def remove_ticket(uuid):
    request(uhttp.DELETE, uuid)


def request(method, uuid, body=None):
    log.debug("Sending request method=%r, ticket=%r, body=%r",
              method, uuid, body)
    con = uhttp.UnixHTTPConnection(DAEMON_SOCK)
    with closing(con):
        try:
            con.request(method, "/tickets/%s" % uuid, body=body)
            res = con.getresponse()
        except (http_client.HTTPException, EnvironmentError) as e:
            raise se.ImageTicketsError("Error communicating with "
                                       "ovirt-imageio-daemon: "
                                       "{error}".format(error=e))

        if res.status >= 300:
            content = _read_content(res)
            raise se.ImageDaemonError(res.status, res.reason, content)


def _read_content(response):
    try:
        content_length = int(response.getheader("content-length",
                                                default=""))
    except ValueError as e:
        error_info = {"explanation": "Invalid content-length",
                      "detail": str(e)}
        raise se.ImageDaemonError(response.status, response.reason, error_info)

    try:
        res_data = response.read(content_length)
    except EnvironmentError as e:
        error_info = {"explanation": "Error reading response",
                      "detail": str(e)}
        raise se.ImageDaemonError(response.status, response.reason, error_info)

    try:
        return json.loads(res_data)
    except ValueError as e:
        error_info = {"explanation": "Invalid JSON", "detail": str(e)}
        raise se.ImageDaemonError(response.status, response.reason, error_info)
