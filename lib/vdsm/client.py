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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
"""
vdsm client

This is a simple client which uses jsonrpc protocol that was introduced as part
of ovirt 3.5.

This client is not aware of the available methods and parameters.
The user should consult the schema to construct the wanted command.

The client is invoked with::

    cli = client.connect(host, port, ssl)

For example::

    cli = client.connect('localhost', 54321, True)

A good practice is to wrap the client calls with utils.closing context manager.
This will ensure closing connection in the end of the client run and better
error handling::

    from vdsm import utils

    with utils.closing(client.connect('localhost', 54321, True)) as cli:
        ...

Invoking commands::

    cli.call(method, args, timeout)

Examples::

    cli.call('Host.getVMList')

    result:
    [u'd7207614-38e3-43c4-b8f2-6086867d0a84',
    u'2c73bed5-cd2a-4d01-9095-97c0d71c831b']

    cli.call('VM.getStats', {'vmID': 'bc26bd11-ee3b-4a56-80d4-770f383a47b9'})

    result:

    [{u'status': u'Down', u'exitMessage': u'Unable to get volume size for
    domain 05558ceb-52c6-4bf4-ab8d-e4d94416eaf0 volume
    73f387f1-1728-4a30-a2de-940e58f9f719',
    u'vmId': u'd7207614-38e3-43c4-b8f2-6086867d0a84', u'exitReason': 1,
    u'timeOffset': u'0', u'statusTime': u'6898991440', u'exitCode': 1}]

ClientError will be raised when we cannot send a request to the server::

    vdsm.client.ClientError: Error sending request: [Errno 111]
    Connection refused

TimeoutError: the request was received by vdsm, but a response hasn't been
received within the specified timeout. The caller is responsible to check the
status of the request in vdsm::

    vdsm.client.TimeoutError: timeout waiting for a response

ServerError: the request was received by vdsm, and execution of the request has
failed::

    vdsm.client.ServerError: Vdsm request failed
    (code=4, message=Virtual machine already exists)

"""


from __future__ import absolute_import

import uuid

from yajsonrpc import stompreactor
import yajsonrpc


def connect(host, port, use_ssl=True):
    try:
        client = stompreactor.SimpleClient(host, port, use_ssl)
    except Exception as e:
        raise ClientError("connect", e)

    return _Client(client)


class Error(Exception):
    """
    Base class for vdsm.client errors
    """
    msg = ""

    def __str__(self):
        return self.msg.format(self=self)


class TimeoutError(Error):
    msg = "Request {self.cmd} timed out after {self.timeout} seconds"

    def __init__(self, cmd, timeout):
        self.cmd = cmd
        self.timeout = timeout


class ClientError(Error):
    msg = "Request {self.cmd} failed: {self.reason}"

    def __init__(self, cmd, reason):
        self.cmd = cmd
        self.reason = reason


class ServerError(Error):
    msg = ("Command {self.cmd} failed (code={self.code}, message="
           "{self.message})")

    def __init__(self, cmd, code, message):
        self.cmd = cmd
        self.code = code
        self.message = message


class _Client(object):
    """
    A wrapper class for client class. Encapulates client run and responsible
    for closing client connection in the end of its run.
    """
    def __init__(self, client):
        self._client = client

    def call(self, method, args=None, timeout=yajsonrpc.CALL_TIMEOUT):
        """
        Client call method, executes a given command

        Args:
            method (string): method name
            args (dict): a dictionary containing all mandatory parameters
            timeout (float): new timeout value in seconds.
                           Note that default timeout is very short and needs to
                           be oviredden for longer tasks (migration for
                           example).

        Returns:
            method result

        Raises:
            ClientError: in case of an error in the protocol.
            TimeoutError: if there is no response after a pre configured time.
            ServerError: in case of an error while executing the command
        """
        if args is None:
            args = {}
        req = yajsonrpc.JsonRpcRequest(method, args, reqId=str(uuid.uuid4()))
        try:
            responses = self._client.call(req, timeout=timeout)
        except EnvironmentError as e:
            raise ClientError(method, e)

        if not responses:
            raise TimeoutError(method, timeout)

        # jsonrpc can handle batch requests so it sends a list of responses,
        # but we call only one verb at a time so responses contains only one
        # item.

        resp = responses[0]
        if resp.error:
            raise ServerError(
                method, resp.error['code'], resp.error['message'])

        return resp.result

    def close(self):
        self._client.close()
