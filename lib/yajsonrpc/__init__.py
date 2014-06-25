# Copyright (C) 2014 Saggi Mizrahi, Red Hat Inc.
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
from Queue import Queue
from weakref import ref
from threading import Lock, Event
from vdsm.utils import traceback

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
    log = logging.getLogger("JsonRpcInvalidRequestError")

    def __init__(self, object_name, msg_content):
        self.log.error("Invalid message found " + msg_content)
        JsonRpcError.__init__(self, -32600,
                              "The JSON sent is not a valid Request object "
                              "with " + object_name)


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
            raise JsonRpcInvalidRequestError("wrong protocol version", obj)

        method = obj.get("method")
        if method is None:
            raise JsonRpcInvalidRequestError("missing method header", obj)

        reqId = obj.get("id")
        if not isinstance(reqId, (str, unicode)):
            raise JsonRpcInvalidRequestError("missing request identifier",
                                             obj)

        params = obj.get('params', [])
        if not isinstance(params, (list, dict)):
            raise JsonRpcInvalidRequestError("wrong params type", obj)

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

        if "result" not in obj and "error" not in obj:
            raise JsonRpcInvalidRequestError("missing result or error info",
                                             obj)

        result = obj.get('result')
        error = JsonRpcError(**obj.get('error'))

        reqId = obj.get('id')
        if not isinstance(reqId, (str, unicode)):
            raise JsonRpcInvalidRequestError("missing response identifier",
                                             obj)
        return JsonRpcResponse(result, error, reqId)

    @staticmethod
    def fromRawObject(obj):
        if obj.get("jsonrpc") != "2.0":
            raise JsonRpcInvalidRequestError("wrong protocol version", obj)

        if "result" not in obj and "error" not in obj:
            raise JsonRpcInvalidRequestError("missing result or error info",
                                             obj)

        result = obj.get("result")
        error = obj.get("error")

        reqId = obj.get("id")
        if not isinstance(reqId, (str, unicode)):
            raise JsonRpcInvalidRequestError("missing response identifier",
                                             obj)

        return JsonRpcResponse(result, error, reqId)


class _JsonRpcClientRequestContext(object):
    def __init__(self, requests, callback):
        self.callback = callback
        self._requests = requests

        self._responses = {}
        for req in requests:
            if req.id is None:
                continue  # Notifications don't have responses

            self._responses[req.id] = None

    def addResponse(self, resp):
        self._responses[resp.id] = resp

    def isDone(self):
        for v in self._responses.values():
            if v is None:
                return False

        return True

    def getResponses(self):
        return self._responses.values()

    def ids(self):
        return self._responses.keys()

    def encode(self):
        return ("[" +
                ", ".join(r.encode() for r in self._requests) +
                "]")


class _JsonRpcServeRequestContext(object):
    def __init__(self, client):
        self._requests = []
        self.client = client
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

        self.client.send(data.encode('utf-8'))

    def addResponse(self, response):
        self._responses.append(response)

    def requestDone(self, response):
        del self._requests[response.id]
        self.addResponse(response)
        self.sendReply()


class JsonRpcClientPool(object):
    def __init__(self, reactor):
        self.log = logging.getLogger("JsonRpcClientPool")
        self._reactor = reactor
        self._inbox = Queue()
        self._clients = {}
        self._eventcbs = []

    def createClient(self, connected_socket):
        transport = self._reactor.createClient(connected_socket)
        transport.setMessageHandler(self._handleClientMessage)
        client = JsonRpcClient(transport)
        self._clients[transport] = client
        return client

    def _handleClientMessage(self, req):
        self._inbox.put_nowait(req)

    def registerEventCallback(self, eventcb):
        self._eventcbs.append(ref(eventcb))

    def unregisterEventCallback(self, eventcb):
        for r in self._eventcbs[:]:
            cb = r()
            if cb is None or cb == eventcb:
                try:
                    self._eventcbs.remove(r)
                except ValueError:
                    # Double unregister, ignore.
                    pass

    def emit(self, client, event, params):
        for r in self._eventcbs[:]:
            cb = r()
            if cb is None:
                continue

            cb(client, event, params)

    def _processEvent(self, client, obj):
        if isinstance(obj, list):
            map(self._processEvent, obj)
            return

        req = JsonRpcRequest.fromRawObject(obj)
        if not req.isNotification():
            self.log.warning("Recieved non notification, ignoring")

        self.emit(client, req.method, req.params)

    def serve(self):
        while True:
            data = self._inbox.get()
            if data is None:
                return

            transport, message = data
            client = self._clients[transport]
            try:
                mobj = json.loads(message)
                isResponse = self._isResponse(mobj)
            except:
                self.log.exception("Problem parsing message from client")
                transport.close()
                del self._clients[transport]
                continue

            if isResponse:
                client._processIncomingResponse(mobj)
            else:
                self._processEvent(client, mobj)

    def _isResponse(self, obj):
            if isinstance(obj, list):
                v = None
                for res in map(self._isResponse, obj):
                    if v is None:
                        v = res

                    if v != res:
                        raise TypeError("batch is mixed")

                return v
            else:
                return ("result" in obj or "error" in obj)

    def close(self):
        self._inbox.put(None)


class JsonRpcCall(object):
    def __init__(self):
        self._ev = Event()
        self.responses = None

    def callback(self, c, resp):
        if not isinstance(resp, list):
            resp = [resp]

        self.responses = resp
        self._ev.set()

    def wait(self, timeout=None):
        self._ev.wait(timeout)
        return self._ev.is_set()

    def isSet(self):
        return self._ev.is_set()


class JsonRpcClient(object):
    def __init__(self, transport):
        self.log = logging.getLogger("jsonrpc.JsonRpcClient")
        self._transport = transport
        self._runningRequests = {}
        self._lock = Lock()

    def setTimeout(self, timeout):
        self._transport.setTimeout(timeout)

    def connect(self):
        self._transport.connect()

    def callMethod(self, methodName, params=[], rid=None):
        return self.call(JsonRpcRequest(methodName, params, rid))

    def call(self, req):
        resp = self.call_batch([req])[0]
        if "error" in resp:
            raise JsonRpcError(resp.error['code'], resp.error['message'])

        return resp.result

    def call_batch(self, *reqs):
        call = self.call_async(reqs)
        call.wait()
        return call.responses

    def call_async(self, *reqs):
        call = JsonRpcCall()
        self.call_cb(call.callback, *reqs)
        return call

    def call_cb(self, cb, *reqs):
        ctx = _JsonRpcClientRequestContext(reqs, cb)
        with self._lock:
            for rid in ctx.ids():
                try:
                    self._runningRequests[rid]
                except KeyError:
                    pass
                else:
                    raise ValueError("Request id already in use %s", rid)

                self._runningRequests[rid] = ctx
                self._transport.send(ctx.encode())

        # All notifications
        if ctx.isDone():
            self._finalizeCtx(ctx)

    def _finalizeCtx(self, ctx):
        with self._lock:
            if not ctx.isDone():
                return

            cb = ctx.callback
            if cb is not None:
                cb(self, ctx.getResponses())

    def _processIncomingResponse(self, resp):
        if isinstance(resp, list):
            map(self._processIncomingResponse, resp)
            return

        resp = JsonRpcResponse.fromRawObject(resp)
        with self._lock:
            ctx = self._runningRequests.pop(resp.id)
            ctx.addResponse(resp)

        self._finalizeCtx(ctx)

    def close(self):
        self._transport.close()


class JsonRpcServer(object):
    log = logging.getLogger("jsonrpc.JsonRpcServer")

    def __init__(self, bridge, threadFactory=None):
        self._bridge = bridge
        self._workQueue = Queue()
        self._threadFactory = threadFactory

    def queueRequest(self, req):
        self.log.debug("Queueing request")
        self._workQueue.put_nowait(req)

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
            server_address = ctx.client.get_local_address()
            self._bridge.register_server_address(server_address)
            if isinstance(req.params, list):
                res = method(*params)
            else:
                res = method(**params)
            self._bridge.unregister_server_address()
        except JsonRpcError as e:
            ctx.requestDone(JsonRpcResponse(None, e, req.id))
        except Exception as e:
            self.log.exception("Internal server error")
            ctx.requestDone(JsonRpcResponse(None,
                                            JsonRpcInternalError(str(e)),
                                            req.id))
        else:
            res = True if res is None else res

            ctx.requestDone(JsonRpcResponse(res, None, req.id))

    @traceback(on=log.name)
    def serve_requests(self):
        while True:
            self.log.debug("Waiting for request")
            obj = self._workQueue.get()
            self.log.debug("Popped request")
            if obj is None:
                break

            client, msg = obj
            self.log.debug("Parsing message")
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
        self.log.info("Stopping JsonRPC Server")
        self._workQueue.put_nowait(None)
