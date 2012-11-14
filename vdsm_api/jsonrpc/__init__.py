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


class JsonRpcRequest(object):
    def __init__(self, method, params, reqId):
        self.method = method
        self.params = params
        self.id = reqId

    def invokeFunction(self, func):
        if isinstance(self.params, list):
            return func(*self.params)
        else:
            return func(**self.params)

    def isNotification(self):
        return self.id is None


class JsonRpcServer(object):
    log = logging.getLogger("JsonRpcServer")

    def __init__(self, bridge):
        self._bridge = bridge

    def _parseMessage(self, msg):
        try:
            return json.loads(msg, 'utf-8')
        except:
            raise JsonRpcParseError()

    def _parseRequest(self, obj):
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

    def _jsonError2Response(self, err, req):
        respId = None
        if req is not None:
            respId = req.id

        return json.dumps({"jsonrpc": "2.0",
                           "error": {"code": err.code, "message": err.message},
                           "id": respId})

    def _generateResponse(self, req, result):
        return json.dumps({"jsonrpc": "2.0",
                          "result": result,
                          "id": req.id}, 'utf-8')

    def handleMessage(self, msgCtx):
        #TODO: support batch requests
        req = None
        resp = None
        try:
            obj = self._parseMessage(msgCtx.data)
            req = self._parseRequest(obj)

            mangledMethod = req.method.replace(".", "_")
            self.log.debug("Looking for method '%s' in bridge",
                           mangledMethod)
            try:
                method = getattr(self._bridge, mangledMethod)
            except AttributeError:
                raise JsonRpcMethodNotFoundError()
            else:
                res = req.invokeFunction(method)
                resp = self._generateResponse(req, res)

        except JsonRpcError as e:
            resp = self._jsonError2Response(e, req)
            self.log.error("Error processing request", exc_info=True)
        except:
            resp = self._jsonError2Response(JsonRpcInternalError(), req)
            self.log.error("Unexpected error while processing request",
                           exc_info=True)

        if resp is None:
            return

        # Notification don't respond even on errors
        if req is not None and req.isNotification():
            return

        msgCtx.sendReply(resp)
