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
from Queue import Queue
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

    def encode(self):
        res = {'jsonrpc': '2.0',
               'method': self.method,
               'params': self.params,
               'id': self.id}

        return json.dumps(res, 'utf-8')

    @staticmethod
    def decode(msg):
        try:
            obj = json.loads(msg, 'utf-8')
        except:
            raise JsonRpcParseError()

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


class _JsonRpcRequestContext(object):
    def __init__(self, ctx, queue, request):
        self.request = request
        self._ctx = ctx
        self._queue = queue

    def isNotification(self):
        return self.id is None

    def sendReply(self, result, error):
        # TBD: Should calling this for a notification raise an error or be
        #      ignored
        resp = JsonRpcResponse(result,
                               error,
                               self.request.id)
        self._queue.put_nowait(_JsonRpcResponseContext(self._ctx, resp))


class _JsonRpcResponseContext(object):
    def __init__(self, ctx, response):
        self.response = response
        self.ctx = ctx


class JsonRpcBatchRequest(object):
    def __init__(self, requests):
        self._requests = requests

    def encode(self):
        obj = [r.toDict() for r in self._requests]
        return json.dumps(obj, 'utf-8')


class JsonRpcCall(object):
    def __init__(self, client, request):
        self._request = request
        self._client = client
        self._state = _STATE_INCOMING

    def fileno(self):
        return self.client.fileno()

    def state(self):
        return


class JsonRpcClient(object):
    def __init__(self, transport):
        self._transport = transport

    def sendRequest(self, request):
        request.encode()
        self.transport.sendMessage()

    def fileno(self):
        return self._transport.fileno()

    def process():
        return None


class JsonRpcServer(object):
    log = logging.getLogger("jsonrpc.JsonRpcServer")

    def __init__(self, bridge, threadFactory=None):
        self._bridge = bridge
        self._workQueue = Queue()
        self._threadFactory = threadFactory

    def _serveRequest(self, ctx):
        req = ctx.request
        mangledMethod = req.method.replace(".", "_")
        self.log.debug("Looking for method '%s' in bridge",
                       mangledMethod)
        try:
            method = getattr(self._bridge, mangledMethod)
        except AttributeError:
            ctx.sendReply(None, JsonRpcMethodNotFoundError())
        else:
            try:
                params = req.params
                if isinstance(req.params, list):
                    res = method(*params)
                else:
                    res = method(**params)
            except JsonRpcError as e:
                ctx.sendReply(None, e)
            except Exception as e:
                ctx.sendReply(None, JsonRpcInternalError(str(e)))
            else:
                return ctx.sendReply(res, None)

    def _processResponse(self, ctx):
        try:
            msg = ctx.response.encode()
        except Exception as e:
            # Probably result failed to be serialized as json
            errResp = JsonRpcResponse(error=JsonRpcInternalError(str(e)),
                                      reqId=ctx.response.id)

            msg = errResp.encode()

        ctx.ctx.sendReply(msg)

    def serve_requests(self):
        while True:
            obj = self._workQueue.get()
            if obj is None:
                break

            if isinstance(obj, _JsonRpcRequestContext):
                if self._threadFactory is None:
                    self._serveRequest(obj)
                else:
                    self._threadFactory(partial(self._serveRequest, obj))
            else:
                self._processResponse(obj)

    def handleMessage(self, msgCtx):
        #TODO: support batch requests
        req = None
        error = None
        try:
            req = JsonRpcRequest.decode(msgCtx.data)
            ctx = _JsonRpcRequestContext(msgCtx, self._workQueue, req)

            self._workQueue.put_nowait(ctx)
            return

        except JsonRpcError as e:
            self.log.error("Error processing request", exc_info=True)
            error = e
        except:
            self.log.error("Unexpected error while processing request",
                           exc_info=True)

            error = JsonRpcInternalError()

        # Notification, don't respond even on errors
        if req is not None and req.isNotification():
            return

        if req is None:
            resp = JsonRpcResponse(None, error, None)
        else:
            resp = JsonRpcResponse(None, error, req.id)

        ctx = _JsonRpcResponseContext(msgCtx, resp)
        self._workQueue.put_nowait(ctx)

    def stop(self):
        self._workQueue.put_nowait(None)
