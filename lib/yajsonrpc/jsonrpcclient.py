# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import json
import logging

import six

from six.moves import queue
from threading import Lock, Event

from yajsonrpc import \
    exception, \
    CALL_TIMEOUT, \
    JsonRpcRequest, \
    Notification, \
    JsonRpcResponse


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
        return list(six.itervalues(self._responses))

    def ids(self):
        return six.iterkeys(self._responses)

    def encode(self):
        return ("[" +
                ", ".join(r.encode() for r in self._requests) +
                "]")


class JsonRpcClient(object):
    def __init__(self, transport):
        self.log = logging.getLogger("jsonrpc.JsonRpcClient")
        transport.set_message_handler(self._handleMessage)
        self._transport = transport
        self._runningRequests = {}
        self._lock = Lock()
        self._event_queues = {}

    def callMethod(self, methodName, params=[], rid=None):
        responses = self.call(JsonRpcRequest(methodName, params, rid))
        if responses is None:
            raise exception.JsonRpcNoResponseError(method=methodName)

        response = responses[0]
        if response.error:
            raise response.error
        else:
            return response.result

    def call(self, *reqs, **kwargs):
        flow_id = kwargs.pop('flow_id', None)
        call = self.call_async(flow_id, *reqs)
        call.wait(kwargs.get('timeout', CALL_TIMEOUT))
        return call.responses

    def call_async(self, flow_id, *reqs):
        call = JsonRpcCall()
        self.call_cb(call.callback, flow_id, *reqs)
        return call

    def call_cb(self, cb, flow_id, *reqs):
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

        self._transport.send(ctx.encode(), flow_id=flow_id)

        # All notifications
        if ctx.isDone():
            self._finalizeCtx(ctx)

    def subscribe(self, queue_name, event_queue=None):
        """
        Subscribe to a queue and listen for messages.

        Received events are pushed to a queue passed as parameter.
        The queue can contain tuples (event_id, params_dictionary)
        or 'None', which signalizes that no more events will
        be received and the client can stop getting from the queue.

        :param queue_name: Name of the STOMP queue
        :param event_queue: Optional; Received events are pushed to this queue.
                            If not set, incoming events will be ignored.
                            The queue instance must have a 'put' method and
                            it must not block.

        :return: Id of the created subscription
        """

        sub_id = self._transport.subscribe(
            queue_name,
            lambda msg: self._handleMessage(msg, event_queue)
        )

        self._event_queues[sub_id] = event_queue
        return sub_id

    def unsubscribe(self, sub):
        """
        Unsubscribe and stop recieving messages.

        :param sub: Id of the subscription returned from subscribe()
        """
        self._transport.unsubscribe(sub)

        # Put 'None' to the queue, to signalize to client that it can stop
        # reading from the queue.
        self._event_queues[sub].put(None)
        del self._event_queues[sub]

    def notify(self, event_id, dest, event_schema, params=None):
        """
        JsonRpcClient notify method, sends an event on a spesific queue

        Args:
            event_id (string): unique event name
            dest (string): destination queue
            event_schema (Schema): a schema for vdsm events
            params (dict): event content

        Returns:
            None
        """
        if not params:
            params = {}

        def send_notification(message):
            self._transport.send(message, dest)

        notification = Notification(event_id, send_notification, event_schema)
        notification.emit(params)

    def _finalizeCtx(self, ctx):
        if not ctx.isDone():
            return

        cb = ctx.callback
        if cb is not None:
            cb(self, ctx.getResponses())

    def _processIncomingResponse(self, resp):
        if isinstance(resp, list):
            for response in resp:
                self._processIncomingResponse(response)
            return

        resp = JsonRpcResponse.fromRawObject(resp)
        with self._lock:
            if resp.id is None:
                self.log.warning(
                    "Got an error from server without an ID (%s)",
                    resp.error,
                )
            ctx = self._runningRequests.pop(resp.id)

        ctx.addResponse(resp)

        self._finalizeCtx(ctx)

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

    def _handleMessage(self, message, event_queue=None):
        try:
            mobj = json.loads(message)
        except ValueError:
            self.log.warning(
                "Received message is not a valid JSON: %r",
                message
            )
            return

        try:
            isResponse = self._isResponse(mobj)
        except TypeError:
            self.log.warning(
                "Received batch contains responses and events, ignoring."
            )
            return

        if isResponse:
            self._processIncomingResponse(mobj)
        else:
            self._processEvent(mobj, event_queue)

    def _processEvent(self, obj, event_queue):
        if not event_queue:
            self.log.debug(
                "No event queue is registered for received event, "
                "ignoring. Event: %s",
                obj
            )
            return

        if isinstance(obj, list):
            for o in obj:
                self._processEvent(o, event_queue)
            return

        req = JsonRpcRequest.fromRawObject(obj)
        if not req.isNotification():
            self.log.warning("Recieved non notification, ignoring")
            return

        try:
            event_queue.put((req.method, req.params))
        except queue.Full:
            self.log.warning("Event queue full, ignoring received event.")

    def close(self):
        sub_ids = list(self._event_queues.keys())
        for sub_id in sub_ids:
            self.unsubscribe(sub_id)

        self._transport.close()

    stop = close


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
        return self.isSet()

    def isSet(self):
        return self._ev.is_set()
