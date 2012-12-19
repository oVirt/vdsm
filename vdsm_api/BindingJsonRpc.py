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

from jsonrpc import JsonRpcServer
from jsonrpc.tcpReactor import TCPReactor
ProtonReactor = None
try:
    from jsonrpc.protonReactor import ProtonReactor
except ImportError:
    pass


class BindingJsonRpc(object):
    log = logging.getLogger('BindingJsonRpc')

    def __init__(self, bridge, backendConfig):
        reactors = []
        self.bridge = bridge
        self.server = JsonRpcServer(bridge)
        for backendType, cfg in backendConfig:
            if backendType == "tcp":
                reactors.append(self._createTcpReactor(cfg))
            elif backendType == "amqp":
                if ProtonReactor is None:
                    continue

                reactors.append(self._createProtonReactor(cfg))

        self._reactors = reactors

    def _createTcpReactor(self, cfg):
        address = cfg.get("ip", "0.0.0.0")
        try:
            port = cfg["port"]
        except KeyError:
            raise ValueError("cfg")

        return TCPReactor((address, port), self.server)

    def _createProtonReactor(self, cfg):
        address = cfg.get("host", "0.0.0.0")
        port = cfg.get("port", 5672)
        return ProtonReactor((address, port), self.server)

    def start(self):
        for reactor in self._reactors:
            reactorName = reactor.__class__.__name__
            try:
                reactor.start_listening()
            except:
                # TBD: propegate error and crash VDSM
                self.log.warning("Could not listen on for rector '%s'",
                                 reactorName)
            else:
                t = threading.Thread(target=reactor.process_requests,
                                     name='JsonRpc (%s)' % reactorName)
                t.setDaemon(True)
                t.start()

        t = threading.Thread(target=self.server.serve_requests,
                             name='JsonRpc (Rquest Processing)')
        t.setDaemon(True)
        t.start()

    def prepareForShutdown(self):
        self.server.stop()
        for reactor in self._reactors:
            reactor.stop()
