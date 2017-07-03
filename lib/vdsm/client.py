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
The user should consult the schema to construct the wanted command:
    https://github.com/oVirt/vdsm/blob/master/lib/api/vdsm-api.yml

The client is invoked with::

    cli = client.connect('localhost', 54321, use_tls=True)

A good practice is to wrap the client calls with utils.closing context manager.
This will ensure closing connection in the end of the client run and better
error handling::

    from vdsm import utils

    with utils.closing(client.connect('localhost', 54321)) as cli:
        ...

Invoking commands::

    cli.Host.getVMList()

    result:
    [u'd7207614-38e3-43c4-b8f2-6086867d0a84',
    u'2c73bed5-cd2a-4d01-9095-97c0d71c831b']

    cli.VM.getStats(vmID='bc26bd11-ee3b-4a56-80d4-770f383a47b9')

    result:

    [{u'status': u'Down', u'exitMessage': u'Unable to get volume size for
    domain 05558ceb-52c6-4bf4-ab8d-e4d94416eaf0 volume
    73f387f1-1728-4a30-a2de-940e58f9f719',
    u'vmId': u'd7207614-38e3-43c4-b8f2-6086867d0a84', u'exitReason': 1,
    u'timeOffset': u'0', u'statusTime': u'6898991440', u'exitCode': 1}]

Default commands timeout is 60 seconds. Please note that the default timeout
is short and needs to be changed for longer tasks (migration, for example).
Default timeout can be set during connection::

    cli = client.connect('localhost', 54321, use_tls=True, timeout=180)

Setting timeout per command::

    cli.Host.getVMList(_timeout=180)

ConnectionError: client can't connect to vdsm::

    vdsm.client.ConnectionError: Connection to localhost:54321 with
    use_tls=True, timeout=60 failed: [Errno 111] Connection refused

MissingSchemaError: there was an error parsing the schema::

    vdsm.client.MissingSchemaError: Error parsing schema: Unable to find API
    schema file

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

import functools
import uuid

from vdsm.api import vdsmapi

from yajsonrpc import stompreactor
from yajsonrpc import stomp

import yajsonrpc


DEFAULT_PORT = 54321


def connect(
        host, port=DEFAULT_PORT, use_tls=True, timeout=60,
        gluster_enabled=False, incoming_heartbeat=stomp.DEFAULT_INCOMING,
        outgoing_heartbeat=stomp.DEFAULT_OUTGOING,
        nr_retries=stomp.NR_RETRIES):
    try:
        client = stompreactor.SimpleClient(
            host, port, use_tls, incoming_heartbeat=incoming_heartbeat,
            outgoing_heartbeat=outgoing_heartbeat, nr_retries=nr_retries)

    except Exception as e:
        raise ConnectionError(host, port, use_tls, timeout, e)

    return _Client(client, timeout, gluster_enabled)


class Error(Exception):
    """
    Base class for vdsm.client errors

    This is a copy of vdsm.common.errors.Base. Kept here so we won't
    have to depend on vdsm-python providing the errors module.

    TODO: depend on vdsm-common package when we have one.
    """
    msg = ""

    def __str__(self):
        return self.msg.format(self=self)


class ConnectionError(Error):
    msg = ("Connection to {self.host}:{self.port} with use_tls={self.use_tls},"
           " timeout={self.timeout} failed: {self.reason}")

    def __init__(self, host, port, use_tls, timeout, reason):
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.timeout = timeout
        self.reason = reason


class MissingSchemaError(Error):
    msg = "Error parsing schema: {self.reason}"

    def __init__(self, reason):
        self.reason = reason


class TimeoutError(Error):
    msg = ("Request {self.cmd} with args {self.params} timed out "
           "after {self.timeout} seconds")

    def __init__(self, cmd, params, timeout):
        self.cmd = cmd
        self.params = params
        self.timeout = timeout


class ClientError(Error):
    msg = ("Request {self.cmd} with args {self.params} failed: {self.reason}")

    def __init__(self, cmd, params, reason):
        self.cmd = cmd
        self.params = params
        self.reason = reason


class ServerError(Error):
    msg = ("Command {self.cmd} with args {self.params} failed:\n"
           "(code={self.code}, message={self.message})")

    def __init__(self, cmd, params, code, message):
        self.cmd = cmd
        self.params = params
        self.code = code
        self.message = message


class Namespace(object):
    def __init__(self, name, call):
        self._name = name
        self._call = call
        self.methods = []

    def __getattr__(self, method_name):
        return functools.partial(self._call, self._name, method_name)


class _Client(object):
    """
    A wrapper class for client class. Encapulates client run and responsible
    for closing client connection in the end of its run.
    """
    def __init__(self, client, default_timeout, gluster_enabled=False):
        self._client = client
        self._default_timeout = default_timeout
        try:
            schema_paths = [vdsmapi.find_schema()]
            if gluster_enabled:
                schema_paths.append(vdsmapi.find_schema('vdsm-api-gluster'))
            self._schema = vdsmapi.Schema(schema_paths, False)
        except vdsmapi.SchemaNotFound as e:
            raise MissingSchemaError(e)
        self._create_namespaces()

    def _create_namespaces(self):
        for method in self._schema.get_methods:
            namespace, method = method.split('.', 1)
            if not hasattr(self, namespace):
                setattr(self, namespace, Namespace(namespace, self._call))
            getattr(self, namespace).methods.append(method)

    def _call(self, namespace, method_name, **kwargs):
        """
        Client call method, executes a given command

        Args:
            namespace (string): namespace name
            method_name (string): method name
            **kwargs: Arbitrary keyword arguments

        Returns:
            method result

        Raises:
            ClientError: in case of an error in the protocol.
            TimeoutError: if there is no response after a pre configured time.
            ServerError: in case of an error while executing the command
        """
        method = namespace + "." + method_name
        timeout = kwargs.pop("_timeout", self._default_timeout)

        req = yajsonrpc.JsonRpcRequest(
            method, kwargs, reqId=str(uuid.uuid4()))
        try:
            responses = self._client.call(req, timeout=timeout)
        except EnvironmentError as e:
            raise ClientError(method, kwargs, e)

        if not responses:
            raise TimeoutError(method, kwargs, timeout)

        # jsonrpc can handle batch requests so it sends a list of responses,
        # but we call only one verb at a time so responses contains only one
        # item.

        resp = responses[0]
        if resp.error:
            raise ServerError(
                method, kwargs, resp.error['code'], resp.error['message'])

        return resp.result

    def close(self):
        self._client.close()
