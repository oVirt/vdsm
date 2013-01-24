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

import logging
import uuid
from Queue import Queue, Empty
import time
from functools import partial
from threading import Event

import proton

FAILED = 0
CONNECTED = 1
AUTHENTICATING = 2


class ProtonContext(object):
    log = logging.getLogger("jsonrpc.ProtonContext")

    def __init__(self, reactor, messageQueue, connector, msg):
        self._reactor = reactor
        self._msg = msg
        self._mq = messageQueue
        self._connector = connector

    @property
    def data(self):
        return self._msg.body

    def sendReply(self, data):
        msg = proton.Message()
        msg.address = self._msg.reply_to
        msg.body = data
        self._mq.put_nowait(msg)
        self.log.debug("Message Queued")
        self._reactor._activate(self._connector, proton.PN_CONNECTOR_WRITABLE)
        self._reactor._wakeup()


class ProtonListener(object):
    def __init__(self, address, reactor):
        self._reactor = reactor
        self._address = address

    def close(self):
        self._reactor._scheduleOp(False, self._reactor._stop_listening,
                                  self._address)


class ProtonReactor(object):
    log = logging.getLogger("jsonrpc.ProtonReactor")

    def __init__(self, messageHandler, deliveryTimeout=5):
        self._messageHandler = messageHandler

        self._isRunning = False

        self._driver = proton.pn_driver()

        self._sessionContexts = []
        self._deliveryTimeout = deliveryTimeout
        self._commandQueue = Queue()
        self._listeners = {}

    def _activate(self, connector, cond):
        self._scheduleOp(False, proton.pn_connector_activate, connector, cond)

    def _convertTimeout(self, timeout):
        """
        Timeouts in python are usually floats representing seconds, this
        converts the conventional python timeout to proton compatible
        millisecond timeouts
        """

        if timeout is None:
            return -1

        return int(timeout * 1000)

    def _waitDriverEvent(self, timeout=None):
        self.log.debug("Waiting for events")
        timeout = self._convertTimeout(timeout)
        proton.pn_driver_wait(self._driver, timeout)

    def _acceptConnectionRequests(self):
        l = proton.pn_driver_listener(self._driver)
        while l:
            self.log.debug("Accepting Connection.")
            connector = proton.pn_listener_accept(l)
            proton.pn_connector_set_context(connector, AUTHENTICATING)

            l = proton.pn_driver_listener(self._driver)

    def _authenticateConnector(self, connector):
        self.log.debug("Authenticating...")
        sasl = proton.pn_connector_sasl(connector)
        state = proton.pn_sasl_state(sasl)
        while state == proton.PN_SASL_CONF or state == proton.PN_SASL_STEP:
            if state == proton.PN_SASL_CONF:
                self.log.debug("Authenticating-CONF...")
                proton.pn_sasl_mechanisms(sasl, "ANONYMOUS")
                proton.pn_sasl_server(sasl)
            elif state == proton.PN_SASL_STEP:
                self.log.debug("Authenticating-STEP...")
                mech = proton.pn_sasl_remote_mechanisms(sasl)
                if mech == "ANONYMOUS":
                    proton.pn_sasl_done(sasl, proton.PN_SASL_OK)
                else:
                    proton.pn_sasl_done(sasl, proton.PN_SASL_AUTH)
            state = proton.pn_sasl_state(sasl)

        if state == proton.PN_SASL_PASS:
            proton.pn_connector_set_connection(connector,
                                               proton.pn_connection())
            proton.pn_connector_set_context(connector, CONNECTED)
            self.log.debug("Authentication-PASSED")
        elif state == proton.PN_SASL_FAIL:
            proton.pn_connector_set_context(connector, FAILED)
            self.log.debug("Authentication-FAILED")
        else:
            self.log.debug("Authentication-PENDING")

    def _processConnectors(self):
        connector = proton.pn_driver_connector(self._driver)
        while connector:
            self.log.debug("Process Connector")

            # releaes any connector that has been closed
            if proton.pn_connector_closed(connector):
                self.log.debug("Closing connector")
                proton.pn_connector_free(connector)
            else:
                proton.pn_connector_process(connector)

                state = proton.pn_connector_context(connector)
                if state == AUTHENTICATING:
                    self._authenticateConnector(connector)
                elif state == CONNECTED:
                    self._serviceConnector(connector)
                else:
                    self.log.warning("Unknown Connection state '%s'" % state)

                proton.pn_connector_process(connector)

            connector = proton.pn_driver_connector(self._driver)

    def _initConnection(self, conn):
        if proton.pn_connection_state(conn) & proton.PN_LOCAL_UNINIT:
            self.log.debug("Connection Opened.")
            proton.pn_connection_open(conn)

    def _openPendingSessions(self, conn):
        ssn = proton.pn_session_head(conn, proton.PN_LOCAL_UNINIT)
        while ssn:
            proton.pn_session_open(ssn)
            ctx = {'connection': conn,
                   'session': ssn,
                   'mqueue': Queue(),
                   'sender': None,
                   'links': []}
            self._sessionContexts.append(ctx)
            proton.pn_session_set_context(ssn, ctx)
            self.log.debug("Session Opened.")
            ssn = proton.pn_session_next(ssn, proton.PN_LOCAL_UNINIT)

    def _openLinks(self, conn):
        link = proton.pn_link_head(conn, proton.PN_LOCAL_UNINIT)
        while link:
            self.log.debug("Opening Link")
            proton.pn_terminus_copy(proton.pn_link_source(link),
                                    proton.pn_link_remote_source(link))
            proton.pn_terminus_copy(proton.pn_link_target(link),
                                    proton.pn_link_remote_target(link))

            ssn = proton.pn_link_session(link)
            if proton.pn_link_is_sender(link):
                for ctx in self._sessionContexts:
                    if ctx['session'] != ssn:
                        continue

                    ctx['links'].append(link)
                self.log.debug("Opening Link to send Events")

            if proton.pn_link_is_receiver(link):
                self.log.debug("Opening Link to recv messages")
                proton.pn_link_flow(link, 1)

            proton.pn_link_open(link)
            link = proton.pn_link_next(link, proton.PN_LOCAL_UNINIT)

    def _processDeliveries(self, conn, connector):
        delivery = proton.pn_work_head(conn)
        while delivery:
            self.log.debug("Process delivery %s" %
                           proton.pn_delivery_tag(delivery))

            if proton.pn_delivery_readable(delivery):
                self._processIncoming(delivery, connector)
            elif proton.pn_delivery_writable(delivery):
                self._processOutgoing(delivery)

            delivery = proton.pn_work_next(delivery)

    def _cleanDeliveries(self, conn):
        link = proton.pn_link_head(conn, (proton.PN_LOCAL_ACTIVE))
        while link:
            d = proton.pn_unsettled_head(link)
            while d:
                _next = proton.pn_unsettled_next(d)
                disp = proton.pn_delivery_remote_state(d)
                age = time.time() - proton.pn_delivery_get_context(d)
                self.log.debug("Checking delivery")
                if disp and disp != proton.PN_ACCEPTED:
                    self.log.warn("Message was not accepted by remote end")

                if disp and proton.pn_delivery_settled(d):
                    self.log.debug("Message settled by remote end")
                    proton.pn_delivery_settle(d)

                elif age > self._deliveryTimeout:
                    self.log.warn("Delivary not settled by remote host")
                    proton.pn_delivery_settle(d)

                elif proton.pn_link_state(link) & proton.PN_REMOTE_CLOSED:
                    self.log.warn("Link closed before settling message")
                    proton.pn_delivery_settle(d)

                d = _next

            link = proton.pn_link_next(link, (proton.PN_LOCAL_ACTIVE))

    def _cleanLinks(self, conn):
        link = proton.pn_link_head(conn, (proton.PN_LOCAL_ACTIVE |
                                          proton.PN_REMOTE_CLOSED))
        while link:
            self.log.debug("Closing Link")
            proton.pn_link_close(link)
            for ctx in self._sessionContexts:
                if link in ctx['links']:
                    ctx['links'].remove(link)

                if link == ctx['sender']:
                    ctx['sender'] = None

            link = proton.pn_link_next(link, (proton.PN_LOCAL_ACTIVE |
                                              proton.PN_REMOTE_CLOSED))

    def _cleanSessions(self, conn):
        ssn = proton.pn_session_head(conn, (proton.PN_LOCAL_ACTIVE |
                                            proton.PN_REMOTE_CLOSED))
        while ssn:
            self.log.debug("Closing Session")
            proton.pn_session_close(ssn)
            self._sessionContexts.remove(proton.pn_session_get_context(ssn))
            ssn = proton.pn_session_next(ssn, (proton.PN_LOCAL_ACTIVE |
                                               proton.PN_REMOTE_CLOSED))

    def _teardownConnection(self, conn):
        if proton.pn_connection_state(conn) == ((proton.PN_LOCAL_ACTIVE |
                                                 proton.PN_REMOTE_CLOSED)):
            proton.pn_connection_close(conn)

    def _queueOutgoingDeliveries(self, conn):
        ctxs = (ctx for ctx in self._sessionContexts
                if ctx['connection'] == conn)

        for ctx in ctxs:
            sender = ctx['sender']

            if sender is None:
                # No sender link
                sender = proton.pn_sender(ctx['session'],
                                          "sender-%s" % str(uuid.uuid4()))
                ctx['sender'] = sender
                proton.pn_link_open(sender)
                continue

            mq = ctx['mqueue']
            try:
                msg = mq.get_nowait()
            except Empty:
                continue
            else:
                self.log.debug("Creating delivery")
                proton.pn_link_set_context(sender, msg.encode())
                if proton.pn_link_credit(sender) == 0:
                    self.log.debug("Not enough credit, waiting")
                    continue

                proton.pn_delivery(sender,
                                   "response-delivery-%s" % str(uuid.uuid4()))

    def _serviceConnector(self, connector):
        self.log.debug("Service Connector")
        conn = proton.pn_connector_connection(connector)

        self._initConnection(conn)
        self._openPendingSessions(conn)
        self._openLinks(conn)
        self._queueOutgoingDeliveries(conn)
        self._processDeliveries(conn, connector)
        self._cleanDeliveries(conn)
        self._cleanLinks(conn)
        self._cleanSessions(conn)

        if proton.pn_connection_state(conn) == (proton.PN_LOCAL_ACTIVE |
                                                proton.PN_REMOTE_CLOSED):
            self.log.debug("Connection Closed")
            proton.pn_connection_close(conn)

    def _processIncoming(self, delivery, connector):
        link = proton.pn_delivery_link(delivery)
        ssn = proton.pn_link_session(link)
        msg = []
        rc, buff = proton.pn_link_recv(link, 1024)
        while rc >= 0:
            msg.append(buff)
            rc, buff = proton.pn_link_recv(link, 1024)

        msg = ''.join(msg)

        proton.pn_delivery_update(delivery, proton.PN_ACCEPTED)
        msgObj = proton.Message()
        msgObj.decode(msg)
        ctx = proton.pn_session_get_context(ssn)
        mq = ctx['mqueue']
        self._messageHandler.handleMessage(ProtonContext(self, mq, connector,
                                                         msgObj))

        proton.pn_delivery_settle(delivery)
        proton.pn_link_advance(link)

        # if more credit is needed, grant it
        if proton.pn_link_credit(link) == 0:
            proton.pn_link_flow(link, 1)

    def _processOutgoing(self, delivery):
        link = proton.pn_delivery_link(delivery)
        msg = proton.pn_link_get_context(link)
        sent = proton.pn_link_send(link, msg)
        if sent < 0:
            self.log.warn("Problem sending message")
        else:
            msg = msg[sent:]
            if len(msg) != 0:
                self.log.debug("Delivery partial")
                proton.pn_link_set_context(link, msg)
            else:
                self.log.debug("Delivery finished")
                proton.pn_link_set_context(link, "")
                proton.pn_delivery_set_context(delivery, time.time())
                proton.pn_link_advance(link)

    def createListener(self, address):
        host, port = address
        l = self._scheduleOp(True, proton.pn_listener, self._driver,
                             host, str(port), None)
        if l is None:
            raise RuntimeError("Could not listen on %s:%s" % (host, port))

        self._listeners[address] = l
        return ProtonListener(address, self)

    def _stop_listening(self, address):
        try:
            l = self._listeners[address]
        except KeyError:
            return

        proton.pn_listener_close(l)
        del self._listeners[address]

    def _emptyCommandQueue(self):
        while True:
            try:
                r = self._commandQueue.get_nowait()
            except Empty:
                return
            else:
                cmd, evt, _ = r
                res = cmd()
                if evt is not None:
                    r[2] = res
                    evt.set()

    def _scheduleOp(self, sync, op, *args, **kwargs):
        if sync:
            r = [partial(op, *args, **kwargs), Event(), None]
        else:
            r = [partial(op, *args, **kwargs), None, None]

        self._commandQueue.put_nowait(r)
        self._wakeup()

        if sync:
            r[1].wait()
            return r[2]

    def process_requests(self):
        self._isRunning = True
        while self._isRunning:
            self._waitDriverEvent()
            self._emptyCommandQueue()
            self._acceptConnectionRequests()
            self._processConnectors()

        for listener in self._listeners.keys():
            self._stop_listening(listener)

    def _wakeup(self):
        proton.pn_driver_wakeup(self._driver)

    def stop(self):
        self._isRunning = False
        self._wakeup()

    def __del__(self):
        proton.pn_driver_free(self._driver)
