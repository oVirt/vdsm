#
# Copyright 2015 Red Hat, Inc.
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

import six.moves.xmlrpc_server
import threading
from _ssl import SSLError
from contextlib import contextmanager
import os
import shutil
import tempfile
import time

from testlib import VdsmTestCase as TestCaseBase
from testlib import expandPermutations, permutations
from vdsm import vdscli
try:
    from vdsm import m2cutils as sslutils
    from integration.m2chelper import get_server_socket
except ImportError:
    from vdsm import sslutils
    from integration.sslhelper import get_server_socket


HOST = '127.0.0.1'


class TestingService():

    def myTest(self):
        time.sleep(0.2)
        return 'test'


class TestServer():

    def __init__(self, useSSL, path):
        self.server = six.moves.xmlrpc_server.SimpleXMLRPCServer(
            (HOST, 0), logRequests=False)
        if useSSL:
            KEY_FILE = os.path.join(path, 'keys/vdsmkey.pem')
            CRT_FILE = os.path.join(path, 'certs/vdsmcert.pem')
            self.server.socket = get_server_socket(KEY_FILE, CRT_FILE,
                                                   self.server.socket)

        _, self.port = self.server.socket.getsockname()
        self.server.register_instance(TestingService())

    def start(self):
        self.thread = threading.Thread(target=self.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def serve_forever(self):
        self.server.serve_forever()

    def stop(self):
        self.server.shutdown()


@contextmanager
def setupclient(useSSL, tsPath,
                timeout=sslutils.SOCKET_DEFAULT_TIMEOUT):
    server = TestServer(useSSL, tsPath)
    server.start()
    hostPort = '0:' + str(server.port)
    client = vdscli.connect(hostPort=hostPort,
                            useSSL=useSSL,
                            tsPath=tsPath,
                            timeout=timeout)
    try:
        yield client
    finally:
        server.stop()


@expandPermutations
class ConnectTest(TestCaseBase):
    def setUp(self):
        self._tmpDir = tempfile.mkdtemp()
        self._tsPath = os.path.join(self._tmpDir, 'pki')

        keys_path = os.path.join(self._tsPath, 'keys')
        certs_path = os.path.join(self._tsPath, 'certs')

        os.makedirs(keys_path)
        os.makedirs(certs_path)
        shutil.copy('server.key', os.path.join(keys_path, 'vdsmkey.pem'))
        shutil.copy('server.crt', os.path.join(certs_path, 'vdsmcert.pem'))
        shutil.copy('server.crt', os.path.join(certs_path, 'cacert.pem'))

    def tearDown(self):
        shutil.rmtree(self._tmpDir)

    @permutations([[True, SSLError], [False, Exception]])
    def testTimeout(self, ssl, error):
        with setupclient(ssl, self._tsPath, timeout=0.1) as client:
            with self.assertRaises(error):
                client.myTest()

    @permutations([[True], [False]])
    def testNoTimeout(self, ssl):
        with setupclient(ssl, self._tsPath) as client:
            with self.assertNotRaises():
                client.myTest()
