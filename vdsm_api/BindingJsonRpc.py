# VDSM JsonRPC Server
# Copyright (C) 2012 Adam Litke, IBM Corporation
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
import threading
import logging
import struct

_Size = struct.Struct("!Q")

from yajsonrpc import JsonRpcServer
from yajsonrpc.asyncoreReactor import AsyncoreReactor
from yajsonrpc.stompReactor import StompReactor
from yajsonrpc.betterAsyncore import SSLContext
ProtonReactor = None
try:
    from yajsonrpc.protonReactor import ProtonReactor
except ImportError:
    pass


def _simpleThreadFactory(func):
    t = threading.Thread(target=func)
    t.setDaemon(False)
    t.start()


class BindingJsonRpc(object):
    log = logging.getLogger('BindingJsonRpc')

    def __init__(self, bridge, backendConfig, truststore_path=None):
        reactors = {}
        self.bridge = bridge
        self.server = JsonRpcServer(bridge,
                                    _simpleThreadFactory)
        self._cfg = backendConfig

        for backendType, cfg in backendConfig:
            if backendType not in reactors:
                if backendType == "tcp":
                    reactors["tcp"] = self._createTcpReactor(truststore_path)
                elif backendType == "stomp":
                    reactors["stomp"] = \
                        self._createStompReactor(truststore_path)
                elif backendType == "amqp":
                    if ProtonReactor is None:
                        continue

                    reactors["amqp"] = self._createProtonReactor()

        self._reactors = reactors

    def _createTcpListener(self, cfg):
        address = cfg.get("ip", "0.0.0.0")
        try:
            port = cfg["port"]
        except KeyError:
            raise ValueError("cfg")

        return self._reactors["tcp"].createListener((address, port),
                                                    self._onAccept)

    def _createStompListener(self, cfg):
        address = cfg.get("ip", "0.0.0.0")
        try:
            port = cfg["port"]
        except KeyError:
            raise ValueError("cfg")

        return self._reactors["stomp"].createListener((address, port),
                                                      self._onAccept)

    def _onAccept(self, listener, client):
        client.setMessageHandler(self.server.queueRequest)

    def _createProtonListener(self, cfg):
        address = cfg.get("host", "0.0.0.0")
        port = cfg.get("port", 5672)
        return self._reactors["amqp"].createListener((address, port))

    def _createTcpReactor(self, truststore_path=None):
        if truststore_path is None:
            return AsyncoreReactor()
        else:
            key_file = truststore_path + '/keys/vdsmkey.pem'
            cert_file = truststore_path + '/certs/vdsmcert.pem'
            ca_cert = truststore_path + '/certs/cacert.pem'
            return AsyncoreReactor(SSLContext(cert_file, key_file, ca_cert))

    def _createStompReactor(self, truststore_path=None):
        if truststore_path is None:
            return StompReactor()
        else:
            key_file = truststore_path + '/keys/vdsmkey.pem'
            cert_file = truststore_path + '/certs/vdsmcert.pem'
            ca_cert = truststore_path + '/certs/cacert.pem'
            return StompReactor(SSLContext(cert_file, key_file, ca_cert))

    def _createProtonReactor(self):
        return ProtonReactor()

    def start(self):
        t = threading.Thread(target=self.server.serve_requests,
                             name='JsonRpcServer')
        t.setDaemon(True)
        t.start()

        for reactor in self._reactors.itervalues():
            reactorName = reactor.__class__.__name__
            t = threading.Thread(target=reactor.process_requests,
                                 name='JsonRpc (%s)' % reactorName)
            t.setDaemon(True)
            t.start()

        for backendType, cfg in self._cfg:
            try:
                if backendType == "tcp":
                    self._createTcpListener(cfg)
                if backendType == "stomp":
                    self._createStompListener(cfg)
                elif backendType == "amqp":
                    self._createProtonListener(cfg)
            except:
                # TBD: propegate error and crash VDSM
                self.log.warning("Could not listen on reactor '%s'",
                                 reactorName, exc_info=True)

    def prepareForShutdown(self):
        self.server.stop()
        for reactor in self._reactors.itervalues():
            reactor.stop()
