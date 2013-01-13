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
import SocketServer
import json
import logging

import struct


_Size = struct.Struct("!Q")


class BindingJsonRpc:
    log = logging.getLogger('BindingJsonRpc')

    def __init__(self, bridge, ip, port):
        self.bridge = bridge
        self.serverPort = port
        self.serverIP = ip
        self._createServer()

    def _createServer(self):
        ip = self.serverIP or '0.0.0.0'
        self.server = JsonRpcServer((ip, self.serverPort), JsonRpcTCPHandler,
                                    self.bridge)

    def start(self):
        t = threading.Thread(target=self.server.serve_forever,
                             name='JsonRpc')
        t.setDaemon(True)
        t.start()

    def prepareForShutdown(self):
        self.server.shutdown()


class JsonRpcServer(SocketServer.TCPServer):
    def __init__(self, addrInfo, handler, bridge):
        self.bridge = bridge
        self.allow_reuse_address = True
        SocketServer.TCPServer.__init__(self, addrInfo, handler)


class JsonRpcTCPHandler(SocketServer.StreamRequestHandler):
    log = logging.getLogger('JsonRpcTCPHandler')

    def handle(self):
        while True:
            # self.request is the TCP socket connected to the client
            try:
                data = self.request.recv(_Size.size)
                if len(data) != _Size.size:
                    self.log.debug("Connection closed")
                    return
                msgLen = _Size.unpack(data)[0]
                msg = json.loads(self.request.recv(msgLen))
            except:
                self.log.warn("Unexpected exception", exc_info=True)
                return
            self.log.debug('Received request: %s', msg)

            try:
                ret = self.server.bridge.dispatch(msg['method'],
                                                  msg.get('params', {}))
                resp = self.buildResponse(msg['id'], ret)
            except Exception as e:
                self.log.warn("Dispatch error", exc_info=True)
                err = {'error': {'code': 5,
                                 'message': 'Dispatch error: %s' % e}}
                resp = self.buildResponse(msg['id'], err)

            self.wfile.write(resp)
            self.wfile.flush()

    def buildResponse(self, msgId, result):
        msgData = {'id': msgId}
        if result['error']['code'] != 0:
            msgData['error'] = result['error']
        else:
            msgData['result'] = result['result']
        msg = json.dumps(msgData)
        msg = msg.encode('utf-8')
        self.log.debug('Sending reply: %s', msg)
        msize = _Size.pack(len(msg))
        return msize + msg
