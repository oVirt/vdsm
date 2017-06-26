#
# Copyright 2014 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import SimpleXMLRPCServer
import threading
from M2Crypto import SSL
from vdsm.m2cutils import CLIENT_PROTOCOL, SSLContext, SSLServerSocket

CERT_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..')
CRT_FILE = os.path.join(CERT_DIR, "server.crt")
KEY_FILE = os.path.join(CERT_DIR, "server.key")
OTHER_CRT_FILE = os.path.join(CERT_DIR, "other.crt")
OTHER_KEY_FILE = os.path.join(CERT_DIR, "other.key")

DEAFAULT_SSL_CONTEXT = SSLContext(
    CRT_FILE, KEY_FILE, session_id="server-tests", protocol=CLIENT_PROTOCOL)


def get_server_socket(key_file, cert_file, socket):
    return SSLServerSocket(raw=socket,
                           keyfile=key_file,
                           certfile=cert_file,
                           ca_certs=cert_file)


class TestServer():

    def __init__(self, host, service):
        self.server = SimpleXMLRPCServer.SimpleXMLRPCServer((host, 0),
                                                            logRequests=False)
        self.server.socket = SSLServerSocket(raw=self.server.socket,
                                             keyfile=KEY_FILE,
                                             certfile=CRT_FILE,
                                             ca_certs=CRT_FILE)
        _, self.port = self.server.socket.getsockname()
        self.server.register_instance(service)

    def start(self):
        self.thread = threading.Thread(target=self.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def serve_forever(self):
        try:
            self.server.serve_forever()
        except SSL.SSLError:
            # expected sslerror is thrown in server side during test_invalid
            # method we do not want to pollute test console output
            pass

    def stop(self):
        self.server.shutdown()

    def get_timeout(self):
        self.server.socket.accept_timeout = 1
        return self.server.socket.accept_timeout + 1
