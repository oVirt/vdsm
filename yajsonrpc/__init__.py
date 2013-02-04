# Copyright (C) 2012 Saggi Mizrahi, Red Hat Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public
# License along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
import json
import logging
from functools import partial

__all__ = ["tcpReactor"]


_STATE_INCOMING = 1
_STATE_OUTGOING = 2
_STATE_ONESHOT = 4


class JsonRpcError(RuntimeError):
    def __init__(self, code, msg):
        self.code = code
        self.message = msg


class JsonRpcParseError(JsonRpcError):
    def __init__(self):
        JsonRpcError.__init__(self, -32700,
                              "Invalid JSON was received by the server. "
                              "An error occurred on the server while parsing "
                              "the JSON text.")


class JsonRpcInvalidRequestError(JsonRpcError):
    def __init__(self):
        JsonRpcError.__init__(self, -32600,
                              "The JSON sent is not a valid Request object.")


class JsonRpcMethodNotFoundError(JsonRpcError):
    def __init__(self):
        JsonRpcError.__init__(self, -32601,
                              "The method does not exist / is not available.")


class JsonRpcInvalidParamsError(JsonRpcError):
    def __init__(self):
        JsonRpcError.__init__(self, -32602,
                              "Invalid method parameter(s).")


class JsonRpcInternalError(JsonRpcError):
    def __init__(self, msg=None):
        if not msg:
            msg = "Internal JSON-RPC error."
        JsonRpcError.__init__(self, -32603, msg)


class JsonRpcRequest(object):
    def __init__(self, method, params=(), reqId=None):
        self.method = method
        self.params = params
        self.id = reqId

    @classmethod
    def decode(cls, msg):
        try:
            obj = json.loads(msg, 'utf-8')
        except:
            raise JsonRpcParseError()

        return cls.fromRawObject(obj)

    @staticmethod
    def fromRawObject(obj):
        if obj.get("jsonrpc") != "2.0":
            raise JsonRpcInvalidRequestError()

        method = obj.get("method")
        if method is None:
            raise JsonRpcInvalidRequestError()

        reqId = obj.get("id")
        if not isinstance(reqId, int):
            raise JsonRpcInvalidRequestError()

        params = obj.get('params', [])
        if not isinstance(params, (list, dict)):
            raise JsonRpcInvalidRequestError()

        return JsonRpcRequest(method, params, reqId)

    def encode(self):
        res = {'jsonrpc': '2.0',
               'method': self.method,
               'params': self.params,
               'id': self.id}

        return json.dumps(res, 'utf-8')

    def isNotification(self):
        return (self.id is None)


class JsonRpcResponse(object):
    def __init__(self, result=None, error=None, reqId=None):
        self.result = result
        self.error = error
        self.id = reqId

    def toDict(self):
        res = {'jsonrpc': '2.0',
               'id': self.id}

        if self.error is not None:
            res['error'] = {'code': self.error.code,
                            'message': self.error.message}
        else:
            res['result'] = self.result

        return res

    def encode(self):
        res = self.toDict()
        return json.dumps(res, 'utf-8')

    @staticmethod
    def decode(msg):
        obj = json.loads(msg, 'utf-8')
        # TODO: More validations
        result = obj.get('result')
        error = JsonRpcError(**obj.get('error'))
        reqId = obj.get('id')
        return JsonRpcResponse(result, error, reqId)


class _JsonRpcServeRequestContext(object):
    def __init__(self, client):
        self._requests = []
        self._client = client
        self._counter = 0
        self._requests = {}
        self._responses = []

    def setRequests(self, requests):
        for request in requests:
            if not request.isNotification():
                self._counter += 1
                self._requests[request.id] = request

        self.sendReply()

    @property
    def counter(self):
        return self._counter

    def sendReply(self):
        if len(self._requests) > 0:
            return

        encodedObjects = []
        for response in self._responses:
            try:
                encodedObjects.append(response.encode())
            except:  # Error encoding data
                response = JsonRpcResponse(None, JsonRpcInternalError,
                                           response.id)
                encodedObjects.append(response.encode())

        if len(encodedObjects) == 1:
            data = encodedObjects[0]
        else:
            data = '[' + ','.join(encodedObjects) + ']'

        self._client.send(data.encode('utf-8'))

    def addResponse(self, response):
        self._responses.append(response)

    def requestDone(self, response):
        del self._requests[response.id]
        self.addResponse(response)
        self.sendReply()


class JsonRpcServer(object):
    log = logging.getLogger("jsonrpc.JsonRpcServer")

    def __init__(self, bridge, messageQueue, threadFactory=None):
        self._bridge = bridge
        self._workQueue = messageQueue
        self._threadFactory = threadFactory

    def _serveRequest(self, ctx, req):
        mangledMethod = req.method.replace(".", "_")
        self.log.debug("Looking for method '%s' in bridge",
                       mangledMethod)
        try:
            method = getattr(self._bridge, mangledMethod)
        except AttributeError:
            if req.isNotification():
                return

            ctx.requestDone(JsonRpcResponse(None,
                                            JsonRpcMethodNotFoundError(),
                                            req.id))
            return

        try:
            params = req.params
            if isinstance(req.params, list):
                res = method(*params)
            else:
                res = method(**params)
        except JsonRpcError as e:
            ctx.requestDone(JsonRpcResponse(None, e, req.id))
        except Exception as e:
            ctx.requestDone(JsonRpcResponse(None,
                                            JsonRpcInternalError(str(e)),
                                            req.id))
        else:
            ctx.requestDone(JsonRpcResponse(res, None, req.id))

    def serve_requests(self):
        while True:
            obj = self._workQueue.get()
            if obj is None:
                break

            client, msg = obj
            self._parseMessage(client, msg)

    def _parseMessage(self, client, msg):
        ctx = _JsonRpcServeRequestContext(client)

        try:
            rawRequests = json.loads(msg)
        except:
            ctx.addResponse(JsonRpcResponse(None, JsonRpcParseError(), None))
            ctx.sendReply()
            return

        if isinstance(rawRequests, list):
            # Empty batch request
            if len(rawRequests) == 0:
                ctx.addResponse(
                    JsonRpcResponse(None, JsonRpcInvalidRequestError(),
                                    None))
                ctx.sendReply()
                return
        else:
            # From this point on we know it's always a list
            rawRequests = [rawRequests]

        # JSON Parsed handling each request
        requests = []
        for rawRequest in rawRequests:
            try:
                req = JsonRpcRequest.fromRawObject(rawRequest)
                requests.append(req)
            except JsonRpcError as err:
                ctx.addResponse(JsonRpcResponse(None, err, None))
            except:
                ctx.addResponse(JsonRpcResponse(None,
                                                JsonRpcInternalError(),
                                                None))

        ctx.setRequests(requests)

        # No request was built successfully or is only notifications
        if ctx.counter == 0:
            ctx.sendReply()

        for request in requests:
            self._runRequest(ctx, request)

    def _runRequest(self, ctx, request):
        if self._threadFactory is None:
            self._serveRequest(ctx, request)
        else:
            self._threadFactory(partial(self._serveRequest, ctx, request))

    def stop(self):
        self._workQueue.put_nowait(None)
