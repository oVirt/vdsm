#
# Copyright 2015 Red Hat, Inc.
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

from functools import partial
from uuid import uuid4

from yajsonrpc import stompreactor
from yajsonrpc import \
    JsonRpcError, \
    JsonRpcRequest, \
    JsonRpcNoResponseError


_COMMAND_CONVERTER = {
    'ping': 'Host.ping',
    'destroy': 'VM.destroy',
    'getVmStats': 'VM.getStats',
    'migrationCreate': 'VM.migrationCreate',
}


class _Server(object):

    def __init__(self, client):
        self._client = client

    def _callMethod(self, methodName, *args):
        try:
            method = _COMMAND_CONVERTER[methodName]
        except KeyError as e:
            raise Exception("Attempt to call function: %s with "
                            "arguments: %s error: %s" %
                            (methodName, args, e))

        req = JsonRpcRequest(method, args, reqId=str(uuid4()))
        responses = self._client.call(req)
        if responses:
            resp = responses[0]
        else:
            raise JsonRpcNoResponseError(method)

        if resp.error is not None:
            raise JsonRpcError(resp.error['code'], resp.error['message'])

        return resp.result

    def migrationCreate(self, params):
        self._callMethod('migrationCreate',
                         params['vmId'],
                         params)
        return {'status': {'code': 0}}

    def __getattr__(self, methodName):
        return partial(self._callMethod, methodName)

    def __del__(self):
        self._client.close()


def connect(client, requestQueue):
    client = stompreactor.StompRpcClient(client,
                                         requestQueue,
                                         str(uuid4()))

    return _Server(client)
