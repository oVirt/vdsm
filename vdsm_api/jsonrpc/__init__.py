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


class _JsonRpcRequest(object):
    def __init__(self, ctx, queue, methodName, params, reqId):
        self.method = methodName
        self.params = params
        self.id = reqId
        self._ctx = ctx
        self._queue = queue

    def invokeFunction(self, func):
        if isinstance(self.params, list):
            return func(*self.params)
        else:
            return func(**self.params)

    def isNotification(self):
        return self.id is None

    def sendReply(self, result, error):
        # TBD: Should calling this for a notification raise an error or be
        #      ignored
        self._queue.put_nowait(_JsonRpcResponse(self._ctx, self.id, result,
                                                error))


class _JsonRpcResponse(object):
    def __init__(self, ctx, reqId, result, error):
        self.ctx = ctx
        self.result = result
        self.error = error
        self.id = reqId


class JsonRpcServer(object):
    log = logging.getLogger("jsonrpc.JsonRpcServer")

    def __init__(self, bridge, threadFactory=None):
        self._bridge = bridge
        self._workQueue = Queue()
        self._threadFactory = threadFactory

    def _parseMessage(self, msg):
        try:
            return json.loads(msg, 'utf-8')
        except:
            raise JsonRpcParseError()

    def _parseRequest(self, obj, ctx, queue):
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

        return _JsonRpcRequest(ctx, queue, method, params, reqId)

    def _jsonError2Response(self, err, req):
        respId = None
        if req is not None:
            respId = req.id

        return json.dumps({"jsonrpc": "2.0",
                           "error": {"code": err.code, "message": err.message},
                           "id": respId})

    def _generateResponse(self, resp):
        res = {"jsonrpc": "2.0",
               "id": resp.id}
        if resp.error is not None:
            res['error'] = {'code': resp.error.code,
                            'message': resp.error.message}
        else:
            res['result'] = resp.result

        return json.dumps(res, 'utf-8')

    def _serveRequest(self, req):
        mangledMethod = req.method.replace(".", "_")
        self.log.debug("Looking for method '%s' in bridge",
                       mangledMethod)
        try:
            method = getattr(self._bridge, mangledMethod)
        except AttributeError:
            req.sendReply(None, JsonRpcMethodNotFoundError())
        else:
            try:
                res = req.invokeFunction(method)
            except JsonRpcError as e:
                req.sendReply(None, e)
            except Exception as e:
                req.sendReply(None, JsonRpcInternalError(str(e)))
            else:
                return req.sendReply(res, None)

    def _processResponse(self, resp):
        try:
            msg = self._generateResponse(resp)
        except Exception as e:
            # Probably result failed to be serialized as json
            errResp = _JsonRpcResponse(resp.ctx,
                                       resp.id,
                                       None,
                                       JsonRpcInternalError(str(e)))

            msg = self._generateResponse(errResp)

        resp.ctx.sendReply(msg)

    def serve_requests(self):
        while True:
            obj = self._workQueue.get()
            if obj is None:
                break

            if isinstance(obj, _JsonRpcRequest):
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
            obj = self._parseMessage(msgCtx.data)
            req = self._parseRequest(obj, msgCtx, self._workQueue)

            self._workQueue.put_nowait(req)
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
            resp = _JsonRpcResponse(msgCtx, None, None, error)
        else:
            resp = _JsonRpcResponse(msgCtx, req.id, None, error)

        self._workQueue.put_nowait(resp)

    def stop(self):
        self._workQueue.put_nowait(None)
