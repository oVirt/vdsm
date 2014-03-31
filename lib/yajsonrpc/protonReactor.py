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

import logging
import uuid
from Queue import Queue, Empty
import time
from functools import partial
from threading import Event

import proton

FAILED = 0
CONNECTED = 1
SERVER_AUTH = 2
CLIENT_AUTH = 3

MBUFF_SIZE = 10


class ProtonError(RuntimeError):
    pass


# Used for reactor coroutines
class Return(object):
    def __init__(self, value):
        self.value = value


class ProtonListener(object):
    def __init__(self, listener, address, reactor, acceptHandler):
        self._listener = listener
        self._reactor = reactor
        self._address = address
        self._acceptHandler = acceptHandler

    def close(self):
        self._reactor._scheduleOp(False, proton.pn_listener_close,
                                  self._listener)


class ProtonClient(object):
    log = logging.getLogger("jsonrpc.ProtonClient")

    def __init__(self, reactor, connection, connector, session, address):
        self._address = address
        self.connector = connector
        self.connection = connection
        self.session = session
        self.sender = None
        self._messageHandler = None
        self._outbox = Queue()
        self._reactor = reactor
        self._connected = False

    def setTimeout(self, timeout):
        # TODO
        pass

    def closed(self):
        return (self.connector is None or
                proton.pn_connector_closed(self.connector))

    def connect(self):
        res = self._reactor._scheduleOp(True, self._openClientSession)
        self.log.debug("Connected successfully to server")
        if res == -1:
            raise ProtonError("Could not connect to server")

    def _openClientSession(self):
        host, port = self._address
        amqpAddress = "ampq://%s:%d/vdsm" % (host, port)
        senderName = "jsonrpc.ProtonClient %s (%s)" % (str(uuid.uuid4()),
                                                       amqpAddress,)
        self.log = logging.getLogger(senderName)

        self.connector = proton.pn_connector(self._reactor._driver,
                                             host, str(port), None)
        if self.connector is None:
            raise ProtonError("Could not create connector")

        self.connection = proton.pn_connection()
        proton.pn_connector_set_connection(self.connector, self.connection)

        sasl = proton.pn_connector_sasl(self.connector)
        proton.pn_sasl_mechanisms(sasl, "ANONYMOUS")
        proton.pn_sasl_client(sasl)

        proton.pn_connector_set_context(self.connector, CLIENT_AUTH)
        self.log.debug("Opening active connection")
        proton.pn_connection_open(self.connection)

        while True:
            # TODO: Handle connection being closed mid authentication
            if proton.pn_sasl_state(sasl) in (proton.PN_SASL_PASS,):
                proton.pn_connector_set_context(self.connector, CONNECTED)
                break

            if proton.pn_sasl_state(sasl) == proton.PN_SASL_FAIL:
                yield Return(-1)

            yield

        self.session = proton.pn_session(self.connection)
        proton.pn_session_open(self.session)
        proton.pn_session_set_context(self.session, self)

        link = proton.pn_sender(self.session, senderName)
        dst = proton.pn_link_target(link)
        proton.pn_terminus_set_address(dst, amqpAddress)
        self.sender = link
        yield Return(1)

    def _pushIncomingMessage(self, msg):
        if self._messageHandler is not None:
            self._messageHandler((self, msg))
        else:
            # Inbox not set
            self.log.warn("Message missed since inbox was not set for "
                          "this client")

    def _popPendingMessage(self):
        return self._outbox.get_nowait()

    def setMessageHandler(self, msgHandler):
        self._messageHandler = msgHandler

    def send(self, msg):
        self._outbox.put_nowait(msg)
        self._reactor._activate(self.connector,
                                proton.PN_CONNECTOR_WRITABLE)

    def close(self):
        # TODO
        pass


class ProtonReactor(object):
    log = logging.getLogger("jsonrpc.ProtonReactor")

    def __init__(self, deliveryTimeout=5, sslctx=None):
        self.sslctx = sslctx
        self.log = logging.getLogger("jsonrpc.ProtonReactor (%d)" % id(self))
        self._isRunning = False
        self._coroutines = []

        self._driver = proton.pn_driver()

        self._deliveryTimeout = deliveryTimeout
        self._commandQueue = Queue()
        self._listeners = {}
        self._wakeEv = Event()

    def _activate(self, connector, cond):
        self._scheduleOp(False, proton.pn_connector_activate,
                         connector, cond)

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
            proton.pn_connector_set_context(connector, SERVER_AUTH)

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
                if isinstance(mech, (list, tuple)):
                    mech = mech[0]

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
        connector = proton.pn_connector_head(self._driver)
        while connector:
            # releaes any connector that has been closed
            if proton.pn_connector_closed(connector):
                self.log.debug("Closing connector")
                proton.pn_connector_free(connector)
            else:
                proton.pn_connector_process(connector)

                state = proton.pn_connector_context(connector)
                if state == SERVER_AUTH:
                    self._authenticateConnector(connector)
                elif state == CONNECTED:
                    self._serviceConnector(connector)
                # Client authentication is handeled in a coroutine
                elif state == CLIENT_AUTH:
                    pass
                else:
                    self.log.warning("Unknown Connection state '%s'" % state)

                proton.pn_connector_process(connector)

            connector = proton.pn_connector_next(connector)

    def _initConnection(self, conn):
        if proton.pn_connection_state(conn) & proton.PN_LOCAL_UNINIT:
            self.log.debug("Connection Opened.")
            proton.pn_connection_open(conn)

    def createClient(self, address):
        return ProtonClient(self, None, None, None, address)

    def _openPendingSessions(self, conn, connector):
        ssn = proton.pn_session_head(conn, proton.PN_LOCAL_UNINIT)
        while ssn:
            proton.pn_session_open(ssn)
            ctx = ProtonClient(self, conn, connector, ssn, None)
            l = proton.pn_connector_listener(connector)
            listener = proton.pn_listener_context(l)
            listener._acceptHandler(listener, ctx)

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
            client = proton.pn_session_get_context(ssn)
            if proton.pn_link_is_sender(link):
                if client.sender != link:
                    self.log.debug("Already have a sender opened for session")
                    proton.pn_link_close(link)
                else:
                    self.log.debug("Opening Link to send messages")
                    proton.pn_link_open(link)

            elif proton.pn_link_is_receiver(link):
                self.log.debug("Opening Link to recv messages")
                proton.pn_link_open(link)
                proton.pn_link_flow(link, MBUFF_SIZE)

            link = proton.pn_link_next(link, proton.PN_LOCAL_UNINIT)

    def _processDeliveries(self, conn, connector):
        delivery = proton.pn_work_head(conn)
        while delivery:
            if proton.pn_delivery_readable(delivery):
                self._processIncoming(delivery, connector)
            elif proton.pn_delivery_writable(delivery):
                self._processOutgoing(delivery)

            delivery = proton.pn_work_next(delivery)

    def _cleanDeliveries(self, conn):
        def link_iter(conn):
            link = proton.pn_link_head(conn, (proton.PN_LOCAL_ACTIVE))
            while link:
                yield link
                link = proton.pn_link_next(link, (proton.PN_LOCAL_ACTIVE))

        def delivery_iter(link):
            d = proton.pn_unsettled_head(link)
            while d:
                yield d
                d = proton.pn_unsettled_next(d)

        for link in link_iter(conn):
            for d in delivery_iter(link):
                ctx = proton.pn_delivery_get_context(d)
                if isinstance(ctx, str):
                    continue

                disp = proton.pn_delivery_remote_state(d)
                age = time.time() - ctx
                self.log.debug("Checking delivery (%s)",
                               proton.pn_delivery_tag(d))

                if disp and disp != proton.PN_ACCEPTED:
                    self.log.warn("Message was not accepted by remote end")

                if disp and proton.pn_delivery_settled(d):
                    self.log.debug("Message settled by remote end")
                    proton.pn_delivery_settle(d)
                    proton.pn_delivery_clear(d)

                elif age > self._deliveryTimeout:
                    self.log.warn("Delivary not settled by remote host")
                    proton.pn_delivery_settle(d)
                    proton.pn_delivery_clear(d)

                elif proton.pn_link_state(link) & proton.PN_REMOTE_CLOSED:
                    self.log.warn("Link closed before settling message")
                    proton.pn_delivery_settle(d)
                    proton.pn_delivery_clear(d)

    def _cleanLinks(self, conn):
        link = proton.pn_link_head(conn, (proton.PN_LOCAL_ACTIVE |
                                          proton.PN_REMOTE_CLOSED))
        while link:
            self.log.debug("Closing Link")
            proton.pn_link_close(link)
            ssn = proton.pn_link_session(link)
            client = proton.pn_session_get_context(ssn)
            if link == client.sender:
                client.sender = None

            link = proton.pn_link_next(link, (proton.PN_LOCAL_ACTIVE |
                                              proton.PN_REMOTE_CLOSED))

    def _cleanSessions(self, conn):
        ssn = proton.pn_session_head(conn, (proton.PN_LOCAL_ACTIVE |
                                            proton.PN_REMOTE_CLOSED))
        while ssn:
            self.log.debug("Closing Session")
            proton.pn_session_close(ssn)
            ssn = proton.pn_session_next(ssn, (proton.PN_LOCAL_ACTIVE |
                                               proton.PN_REMOTE_CLOSED))

    def _teardownConnection(self, conn):
        if proton.pn_connection_state(conn) == ((proton.PN_LOCAL_ACTIVE |
                                                 proton.PN_REMOTE_CLOSED)):
            proton.pn_connection_close(conn)

    def _iterSessions(self, conn, flags):
        ssn = proton.pn_session_head(conn, flags)
        while ssn:
            yield ssn
            ssn = proton.pn_session_next(ssn, flags)

    def _queueOutgoingDeliveries(self, conn):
        for ssn in self._iterSessions(conn, proton.PN_LOCAL_ACTIVE):
            ctx = proton.pn_session_get_context(ssn)
            sender = ctx.sender

            if sender is None:
                # No sender link
                sender = proton.pn_sender(ctx.session,
                                          "sender-%s" % str(uuid.uuid4()))
                ctx.sender = sender
                continue

            while proton.pn_link_credit(sender) > 0:
                try:
                    data = ctx._popPendingMessage()
                except Empty:
                    break
                else:
                    msg = proton.Message()
                    msg.body = data
                    d = proton.pn_delivery(sender,
                                           "delivery-%s" % str(uuid.uuid4()))

                    proton.pn_delivery_set_context(d, msg.encode())
                    self.log.debug("Queueing delivery (%s)",
                                   proton.pn_delivery_tag(d))

    def _serviceConnector(self, connector):
        conn = proton.pn_connector_connection(connector)

        self._initConnection(conn)
        self._openPendingSessions(conn, connector)
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
        self.log.debug("Receiving '%s'", proton.pn_delivery_tag(delivery))
        while True:
            rc, buff = proton.pn_link_recv(link, 1024)
            msg.append(buff)
            if rc == proton.PN_EOS:
                break

        msg = ''.join(msg)

        self.log.debug("Received '%s'", proton.pn_delivery_tag(delivery))
        proton.pn_link_advance(link)
        proton.pn_delivery_update(delivery, proton.PN_ACCEPTED)
        proton.pn_delivery_settle(delivery)

        msgObj = proton.Message()
        msgObj.decode(msg)
        ctx = proton.pn_session_get_context(ssn)
        ctx._pushIncomingMessage(msgObj.body)

        # if more credit is needed, grant it
        if proton.pn_link_credit(link) == 0:
            proton.pn_link_flow(link, MBUFF_SIZE)

    def _processOutgoing(self, delivery):
        link = proton.pn_delivery_link(delivery)
        msg = proton.pn_delivery_get_context(delivery)
        proton.pn_link_send(link, msg)
        if proton.pn_link_advance(link):
            self.log.debug("Delivery finished (%s)",
                           proton.pn_delivery_tag(delivery))
            proton.pn_delivery_set_context(delivery, time.time())

    def createListener(self, address, acceptHandler):
        host, port = address
        return self._scheduleOp(True, self._createListener, address,
                                acceptHandler)

    def _createListener(self, address, acceptHandler):
        host, port = address
        l = proton.pn_listener(self._driver, host, str(port), None)
        if l is None:
            raise RuntimeError("Could not listen on %s:%s" % (host, port))

        lObj = ProtonListener(l, address, self, acceptHandler)
        proton.pn_listener_set_context(l, lObj)
        return lObj

    def _emptyCommandQueue(self):
        while True:
            try:
                r = self._commandQueue.get_nowait()
            except Empty:
                return
            else:
                cmd, evt, _ = r
                res = cmd()
                if hasattr(res, "next"):
                    self._coroutines.append((res, r))

                elif evt is not None:
                    r[2] = res
                    evt.set()

    def _processCoroutines(self):
        for cr, req in self._coroutines[:]:
            res = cr.next()
            if isinstance(res, Return):
                cr.close()
                evt = req[1]
                self._coroutines.remove((cr, req))
                if evt is not None:
                    req[2] = res.value
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
            self._processCoroutines()

        l = proton.pn_listener_head(self._driver)
        while l:
            proton.pn_listener_close(l)
            l = proton.pn_listener_next(l)

    def _wakeup(self):
        proton.pn_driver_wakeup(self._driver)

    def stop(self):
        self._isRunning = False
        self._wakeup()

    def __del__(self):
        proton.pn_driver_free(self._driver)
