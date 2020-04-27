# Copyright (C) 2014-2019 Red Hat Inc.
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
from __future__ import absolute_import
from __future__ import division
import logging
from six.moves import queue

from vdsm.common import exception as vdsmexception

from vdsm.common.compat import json
from vdsm.common.logutils import Suppressed, traceback
from vdsm.common.threadlocal import vars
from vdsm.common.time import monotonic_time, event_time
from vdsm.common.password import protect_passwords, unprotect_passwords

from yajsonrpc import exception

__all__ = ["betterAsyncore", "stompserver", "stomp"]

CALL_TIMEOUT = 15

_STATE_INCOMING = 1
_STATE_OUTGOING = 2
_STATE_ONESHOT = 4


class JsonRpcRequest(object):
    def __init__(self, method, params=(), reqId=None):
        self.method = method
        self.params = params
        self.id = reqId

    @classmethod
    def decode(cls, msg):
        try:
            obj = json.loads(msg)
        except:
            raise exception.JsonRpcParseError()

        return cls.fromRawObject(obj)

    @staticmethod
    def fromRawObject(obj):
        if obj.get("jsonrpc") != "2.0":
            raise exception.JsonRpcInvalidRequestError(
                "Wrong protocol version",
                request=obj
            )

        method = obj.get("method")
        if method is None:
            raise exception.JsonRpcInvalidRequestError(
                "missing method header in method",
                request=obj
            )

        reqId = obj.get("id")
        # when sending notifications id is not provided

        params = obj.get('params', [])
        if not isinstance(params, (list, dict)):
            raise exception.JsonRpcInvalidRequestError(
                "wrong params type",
                request=obj
            )

        return JsonRpcRequest(method, protect_passwords(params), reqId)

    def __repr__(self):
        return repr(self.toDict())

    def toDict(self):
        return {
            'jsonrpc': '2.0',
            'method': self.method,
            'params': self.params,
            'id': self.id
        }

    def encode(self):
        res = self.toDict()
        return json.dumps(res)

    def isNotification(self):
        return (self.id is None)


class JsonRpcResponse(object):
    def __init__(self, result=None, error=None, reqId=None):
        self.result = unprotect_passwords(result)
        self.error = error
        self.id = reqId

    def toDict(self):
        res = {'jsonrpc': '2.0',
               'id': self.id}

        if self.error is not None:
            res['error'] = {'code': self.error.code,
                            'message': str(self.error)}
        else:
            res['result'] = self.result

        return res

    def encode(self):
        res = self.toDict()
        return json.dumps(res)

    @staticmethod
    def decode(msg):
        obj = json.loads(msg)
        return JsonRpcResponse.fromRawObject(obj)

    @staticmethod
    def fromRawObject(obj):
        if obj.get("jsonrpc") != "2.0":
            raise exception.JsonRpcInvalidRequestError(
                "wrong protocol version",
                request=obj
            )

        if "result" not in obj and "error" not in obj:
            raise exception.JsonRpcInvalidRequestError(
                "missing result or error info",
                request=obj
            )

        result = obj.get("result")

        error = None
        if "error" in obj:
            error = exception.JsonRpcServerError.from_dict(obj["error"])

        reqId = obj.get("id")

        return JsonRpcResponse(result, error, reqId)


class Notification(object):
    """
    Represents jsonrpc notification message. It builds proper jsonrpc
    notification and pass it a callback which is responsible for
    sending it.
    """
    log = logging.getLogger("jsonrpc.Notification")

    def __init__(self, event_id, cb, event_schema):
        self._event_id = event_id
        self._cb = cb
        self._event_schema = event_schema

    def emit(self, params):
        """
        emit method, builds notification message and sends it.

        Args:
            params(dict): event content

        Returns: None
        """
        self._add_notify_time(params)
        self._event_schema.verify_event_params(self._event_id, params)
        notification = json.dumps({'jsonrpc': '2.0',
                                   'method': self._event_id,
                                   'params': params})

        self.log.debug("Sending event %s", notification)
        self._cb(notification)

    def _add_notify_time(self, body):
        body['notify_time'] = event_time()


class _JsonRpcServeRequestContext(object):
    def __init__(self, client, server_address, context):
        self._requests = []
        self._client = client
        self._server_address = server_address
        self._context = context
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

    @property
    def server_address(self):
        return self._server_address

    @property
    def context(self):
        return self._context

    def sendReply(self):
        if len(self._requests) > 0:
            return

        encodedObjects = []
        for response in self._responses:
            try:
                encodedObjects.append(response.encode())
            except:  # Error encoding data
                response = JsonRpcResponse(None,
                                           exception.JsonRpcInternalError(),
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
        try:
            del self._requests[response.id]
        except KeyError:
            # ignore when response had no id
            # we wouldn't be able to match it
            # with request on the client side
            pass
        self.addResponse(response)
        self.sendReply()


class JsonRpcTask(object):

    def __init__(self, handler, ctx, req):
        self._handler = handler
        self._ctx = ctx
        self._req = req

    def __call__(self):
        self._handler(self._ctx, self._req)

    def __repr__(self):
        return '<JsonRpcTask %s at 0x%x>' % (
            self._req, id(self)
        )


class JsonRpcServer(object):
    log = logging.getLogger("jsonrpc.JsonRpcServer")

    """
    Creates new JsonrRpcServer by providing a bridge, timeout in seconds
    which defining how often we should log connections stats and thread
    factory.
    """
    def __init__(self, bridge, timeout, cif, threadFactory=None):
        self._bridge = bridge
        self._cif = cif
        self._workQueue = queue.Queue()
        self._threadFactory = threadFactory
        self._timeout = timeout
        self._next_report = monotonic_time() + self._timeout
        self._counter = 0

    def queueRequest(self, req):
        self._workQueue.put_nowait(req)

    """
    Aggregates number of requests received by vdsm. Each request from
    a batch is added separately. After time defined by timeout we log
    number of requests.
    """
    def _attempt_log_stats(self):
        self._counter += 1
        if monotonic_time() > self._next_report:
            self.log.info('%s requests processed during %s seconds',
                          self._counter, self._timeout)
            self._next_report += self._timeout
            self._counter = 0

    def _serveRequest(self, ctx, req):
        start_time = monotonic_time()
        response = self._handle_request(req, ctx)
        error = getattr(response, "error", None)
        if error is None:
            response_log = "succeeded"
        else:
            response_log = "failed (error %s)" % (error.code,)
        self.log.info("RPC call %s %s in %.2f seconds",
                      req.method, response_log, monotonic_time() - start_time)
        if response is not None:
            ctx.requestDone(response)

    def _handle_request(self, req, ctx):
        self._attempt_log_stats()
        logLevel = logging.DEBUG

        # VDSM should never respond to any request before all information about
        # running VMs is recovered, see https://bugzilla.redhat.com/1339291
        if not self._cif.ready:
            self.log.info("In recovery, ignoring '%s' in bridge with %s",
                          req.method, req.params)
            return JsonRpcResponse(
                None, vdsmexception.RecoveryInProgress(), req.id)

        self.log.log(logLevel, "Calling '%s' in bridge with %s",
                     req.method, req.params)
        try:
            method = self._bridge.dispatch(req.method)
        except exception.JsonRpcMethodNotFoundError as e:
            if req.isNotification():
                return None

            return JsonRpcResponse(None, e, req.id)

        vars.context = ctx.context
        try:
            params = req.params
            self._bridge.register_server_address(ctx.server_address)
            if isinstance(req.params, list):
                res = method(*params)
            else:
                res = method(**params)
            self._bridge.unregister_server_address()
        except vdsmexception.VdsmException as e:
            return JsonRpcResponse(None, e, req.id)
        except Exception as e:
            self.log.exception("Internal server error")
            return JsonRpcResponse(
                None, exception.JsonRpcInternalError(str(e)), req.id)
        else:
            res = True if res is None else res
            self.log.log(logLevel, "Return '%s' in bridge with %s",
                         req.method, res)
            if isinstance(res, Suppressed):
                res = res.value
            return JsonRpcResponse(res, None, req.id)
        finally:
            vars.context = None

    @traceback(log=log)
    def serve_requests(self):
        while True:
            obj = self._workQueue.get()
            if obj is None:
                break

            self._parseMessage(obj)

    def _parseMessage(self, obj):
        client, server_address, context, msg = obj
        ctx = _JsonRpcServeRequestContext(client, server_address, context)

        try:
            rawRequests = json.loads(msg)
        except:
            ctx.addResponse(JsonRpcResponse(
                None, exception.JsonRpcParseError(), None))
            ctx.sendReply()
            return

        if isinstance(rawRequests, list):
            # Empty batch request
            if len(rawRequests) == 0:
                ctx.addResponse(
                    JsonRpcResponse(
                        None, exception.JsonRpcInvalidRequestError(
                            "request batch is empty", request=rawRequests),
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
            except vdsmexception.VdsmException as err:
                ctx.addResponse(JsonRpcResponse(None, err, None))
            except:
                ctx.addResponse(JsonRpcResponse(
                    None, exception.JsonRpcInternalError(), None))

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
            try:
                self._threadFactory(
                    JsonRpcTask(self._serveRequest, ctx, request)
                )
            except vdsmexception.ContextException as e:
                ctx.requestDone(JsonRpcResponse(None, e, request.id))
            except Exception as e:
                self.log.exception("could not serve request %s", request)
                ctx.requestDone(
                    JsonRpcResponse(
                        None,
                        exception.JsonRpcInternalError(
                            str(e)
                        ),
                        request.id
                    )
                )

    def stop(self):
        self.log.info("Stopping JsonRPC Server")
        self._workQueue.put_nowait(None)
