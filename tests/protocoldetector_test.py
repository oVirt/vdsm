#
# Copyright 2014-2017 Red Hat, Inc.
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

import errno
import socket
import ssl
import threading
import time
from contextlib import contextmanager

from yajsonrpc.betterAsyncore import Reactor
from vdsm.protocoldetector import MultiProtocolAcceptor
from testValidation import broken_on_ci
from testlib import VdsmTestCase, expandPermutations, permutations

from integration.sslhelper import KEY_FILE, CRT_FILE, DEAFAULT_SSL_CONTEXT


class Detector(object):
    """
    A detector returning response to the client, so we can tell if detection
    was successful and transport is useable after detection.
    """

    # Must be defined by sub classes
    NAME = None
    REQUIRED_SIZE = None

    def detect(self, data):
        return data.startswith(self.NAME)

    def handle_socket(self, client_socket, socket_address):

        def run():
            # Wait to detect case where the event loop steals data from the
            # socket after the socket was removed from the event loop.
            time.sleep(0.05)
            try:
                request = ""
                while "\n" not in request:
                    chunk = client_socket.recv(1024)
                    if not chunk:
                        return
                    request += chunk

                response = self.response(request)
                client_socket.sendall(response)
            finally:
                client_socket.shutdown(socket.SHUT_RDWR)
                client_socket.close()

        t = threading.Thread(target=run)
        t.daemon = True
        t.start()


class Echo(Detector):
    """ A detector echoing sent line """

    NAME = "echo"
    REQUIRED_SIZE = len(NAME)

    def response(self, data):
        return data


class Uppercase(Detector):
    """ A detector echoing sent line in UPPERCASE """

    NAME = "uppercase"
    REQUIRED_SIZE = len(NAME)

    def response(self, data):
        return data.upper()


@expandPermutations
class AcceptorTests(VdsmTestCase):

    TIMEOUT = 2.0
    GRACETIME = 0.5
    CONCURRENCY = 5
    PERMUTATIONS = ((False,), (True,))
    SSLCTX = DEAFAULT_SSL_CONTEXT
    BUFSIZE = 512

    def setUp(self):
        self.reactor = None
        self.acceptor = None
        self.acceptor_address = None

    def tearDown(self):
        if self.acceptor:
            self.acceptor.stop()

    # Testing

    def test_reject_ssl_accept_error(self):
        self.start_acceptor(use_ssl=True)
        with self.connect(use_ssl=False) as client:
            client.sendall("this is not ssl handshake\n")
            self.assertRaises(socket.error, client.recv, self.BUFSIZE)

    @permutations(PERMUTATIONS)
    def test_reject_unknown_protocol(self, use_ssl):
        self.start_acceptor(use_ssl)
        self.check_reject(use_ssl)

    @permutations(PERMUTATIONS)
    def test_reject_concurrency(self, use_ssl):
        self.start_acceptor(use_ssl)
        self.check_concurrently(self.check_reject, use_ssl)

    @permutations(PERMUTATIONS)
    def test_detect_echo(self, use_ssl):
        self.start_acceptor(use_ssl)
        data = "echo testing is fun\n"
        self.check_detect(use_ssl, data, data)

    @broken_on_ci("IPv6 not supported on travis", name="TRAVIS_CI")
    @permutations(PERMUTATIONS)
    def test_detect_echo6(self, use_ssl):
        self.start_acceptor(use_ssl, address='::1')
        data = "echo testing is fun\n"
        self.check_detect(use_ssl, data, data)

    @permutations(PERMUTATIONS)
    def test_detect_uppercase(self, use_ssl):
        self.start_acceptor(use_ssl)
        data = "uppercase testing is fun\n"
        self.check_detect(use_ssl, data, data.upper())

    @permutations(PERMUTATIONS)
    def test_detect_concurrency(self, use_ssl):
        self.start_acceptor(use_ssl)
        data = "echo testing is fun\n"
        self.check_concurrently(self.check_detect, use_ssl, data, data)

    @permutations(PERMUTATIONS)
    def test_detect_slow_client(self, use_ssl):
        self.start_acceptor(use_ssl)
        self.check_slow_client(use_ssl)

    @permutations(PERMUTATIONS)
    def test_detect_slow_client_concurrency(self, use_ssl):
        self.start_acceptor(use_ssl)
        self.check_concurrently(self.check_slow_client, use_ssl)

    @permutations(PERMUTATIONS)
    def test_reject_very_slow_client(self, use_ssl):
        self.start_acceptor(use_ssl)
        self.check_very_slow_client(use_ssl)

    @permutations(PERMUTATIONS)
    def test_reject_very_slow_client_concurrency(self, use_ssl):
        self.start_acceptor(use_ssl)
        self.check_concurrently(self.check_very_slow_client, use_ssl)

    # Checking

    def check_detect(self, use_ssl, request, response):
        with self.connect(use_ssl) as client:
            client.sendall(request)
            self.assertEqual(client.recv(self.BUFSIZE), response)

    def check_reject(self, use_ssl):
        with self.connect(use_ssl) as client:
            client.sendall("no such protocol\n")
            self.check_disconnected(client)

    def check_slow_client(self, use_ssl):
        with self.connect(use_ssl) as client:
            time.sleep(self.acceptor.TIMEOUT - self.GRACETIME)
            data = "echo let me in\n"
            client.sendall(data)
            self.assertEqual(client.recv(self.BUFSIZE), data)

    def check_very_slow_client(self, use_ssl):
        with self.connect(use_ssl) as client:
            time.sleep(self.acceptor.TIMEOUT * 2 + self.GRACETIME)
            client.sendall("echo too slow probably\n")
            self.check_disconnected(client)

    def check_disconnected(self, client):
        try:
            data = client.recv(self.BUFSIZE)
        except socket.error as e:
            self.assertEqual(e.errno, errno.ECONNRESET)
        else:
            self.assertEqual(data, '')

    # Helpers

    def start_acceptor(self, use_ssl, address='127.0.0.1'):
        self.reactor = Reactor()
        self.acceptor = MultiProtocolAcceptor(
            self.reactor,
            address,
            0,
            sslctx=self.SSLCTX if use_ssl else None
        )
        self.acceptor.TIMEOUT = 1
        self.acceptor.add_detector(Echo())
        self.acceptor.add_detector(Uppercase())
        self.acceptor_address = \
            self.acceptor._acceptor.socket.getsockname()[0:2]
        t = threading.Thread(target=self.reactor.process_requests)
        t.deamon = True
        t.start()

    @contextmanager
    def connect(self, use_ssl):
        host, port = self.acceptor_address
        addrinfo = socket.getaddrinfo(host, port,
                                      socket.AF_UNSPEC, socket.SOCK_STREAM)
        family, socktype, proto, _, sockaddr = addrinfo[0]
        s = socket.socket(family, socktype, proto)
        try:
            s.settimeout(self.TIMEOUT)
            if use_ssl:
                s = ssl.wrap_socket(s, KEY_FILE, CRT_FILE, ca_certs=CRT_FILE,
                                    server_side=False)
            s.connect(sockaddr)
            yield s
        finally:
            s.close()

    def check_concurrently(self, func, *args, **kw):
        done = [False] * self.CONCURRENCY

        def run(i):
            func(*args, **kw)
            done[i] = True

        threads = []
        try:
            for i in range(self.CONCURRENCY):
                t = threading.Thread(target=run, args=(i,))
                t.daemon = True
                t.start()
                threads.append(t)
        finally:
            for t in threads:
                t.join()

        self.assertTrue(all(done))
