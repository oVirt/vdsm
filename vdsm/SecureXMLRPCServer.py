# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# * Redistributions of source code must retain the above copyright
#   notice, this list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright
#   notice, this list of conditions and the following disclaimer in
#   the documentation and/or other materials provided with the
#   distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.


"""SecureXMLRPCServer.py - simple XML RPC server supporting SSL.
"""

import SimpleXMLRPCServer
import xmlrpclib
import ssl
import httplib
import socket
import SocketServer

SecureXMLRPCRequestHandler = SimpleXMLRPCServer.SimpleXMLRPCRequestHandler

class SecureXMLRPCServer(SimpleXMLRPCServer.SimpleXMLRPCServer):
    def __init__(self, addr,
                 requestHandler=SimpleXMLRPCServer.SimpleXMLRPCRequestHandler,
                 logRequests=True, allow_none=False, encoding=None,
                 bind_and_activate=True,
                 keyfile=None, certfile=None, ca_certs=None,
                 timeout=None):
        """Initialize a SimpleXMLRPCServer instance but wrap its .socket member with ssl."""

        SimpleXMLRPCServer.SimpleXMLRPCServer.__init__(self, addr,
                 requestHandler,
                 logRequests, allow_none, encoding,
                 bind_and_activate=False)
        self.socket = ssl.wrap_socket(self.socket,
                 keyfile=keyfile, certfile=certfile,
                 ca_certs=ca_certs, server_side=True,
                 cert_reqs=ssl.CERT_REQUIRED,
                 do_handshake_on_connect=False)
        if timeout is not None:
            self.socket.settimeout = timeout
        if bind_and_activate:
            self.server_bind()
            self.server_activate()

    def finish_request(self, request, client_address):
        request.do_handshake()

        return SimpleXMLRPCServer.SimpleXMLRPCServer.finish_request(self, request,
                                                             client_address)

    def handle_error(self, request, client_address):
        import logging
        logging.error('client %s', client_address, exc_info=True)


class SecureThreadedXMLRPCServer(SocketServer.ThreadingMixIn,
                                 SecureXMLRPCServer): pass

class VerifyingHTTPSConnection(httplib.HTTPSConnection):
    def __init__(self, host, port=None, key_file=None, cert_file=None,
                 strict=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                 ca_certs=None, cert_reqs=ssl.CERT_REQUIRED):
        httplib.HTTPSConnection.__init__(self, host, port, key_file, cert_file,
                      strict, timeout)
        self.ca_certs = ca_certs
        self.cert_reqs = cert_reqs

    def connect(self):
        "Connect to a host on a given (SSL) port."

        sock = socket.create_connection((self.host, self.port), self.timeout)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
        # DK added: pass ca_cert to sslsocket
        self.sock = ssl.wrap_socket(sock, self.key_file, self.cert_file,
                                    ca_certs=self.ca_certs, server_side=False,
                                    cert_reqs=self.cert_reqs)

class VerifyingSafeTransport(xmlrpclib.SafeTransport):
    def __init__(self, use_datetime=0, key_file=None, cert_file=None,
                 ca_certs=None, cert_reqs=ssl.CERT_REQUIRED):
        xmlrpclib.SafeTransport.__init__(self, use_datetime)
        self.key_file = key_file
        self.cert_file = cert_file
        self.ca_certs = ca_certs
        self.cert_reqs = cert_reqs

    def make_connection(self, host):
        """Return VerifyingHTTPS object that is aware of ca_certs, and will
        create VerifyingHTTPSConnection.
        In Python 2.7, return VerifyingHTTPSConnection object"""
        chost, self._extra_headers, x509 = self.get_host_info(host)
        if hasattr(xmlrpclib.SafeTransport, "single_request"): # Python 2.7
            return VerifyingHTTPSConnection(
                        chost, None, key_file=self.key_file, strict=None,
                        cert_file=self.cert_file, ca_certs=self.ca_certs,
                        cert_reqs=self.cert_reqs)
        else:
            return VerifyingHTTPS(
                        chost, None, key_file=self.key_file,
                        cert_file=self.cert_file, ca_certs=self.ca_certs,
                        cert_reqs=self.cert_reqs)


class VerifyingHTTPS(httplib.HTTPS):
    _connection_class = VerifyingHTTPSConnection

    def __init__(self, host='', port=None, key_file=None, cert_file=None,
                 strict=None, ca_certs=None, cert_reqs=ssl.CERT_REQUIRED):
        """A ca_cert-aware HTTPS object, that creates a VerifyingHTTPSConnection"""
        # provide a default host, pass the X509 cert info

        # urf. compensate for bad input.
        if port == 0:
            port = None
        self._setup(self._connection_class(host, port, key_file,
                                           cert_file, strict, ca_certs=ca_certs,
                                           cert_reqs=cert_reqs))

        # we never actually use these for anything, but we keep them
        # here for compatibility with post-1.5.2 CVS.
        self.key_file = key_file
        self.cert_file = cert_file


class __Test(object):
    """Self-signed key, generated with
    make -C /etc/pki/tls/certs /tmp/selfsign.pem
    with CN=127.0.0.1
    """
    KEYFILE = CERTFILE = CACERT = 'selfsign.pem'
    host = '127.0.0.1'
    port = 8443

    def server(self):
        """Test xml rpc over https server"""
        class xmlrpc_registers:
            def add(self, x, y):
                return x + y

            def wait(self):
                import time
                time.sleep(10)
                return 1

        server = SecureXMLRPCServer((self.host, self.port),
                     keyfile=self.KEYFILE, certfile=self.CERTFILE, ca_certs=self.CACERT)
        server.register_instance(xmlrpc_registers())
        print "Serving HTTPS on", self.host, "port", self.port
        server.serve_forever()

    def client(self):
        vtransport=VerifyingSafeTransport(key_file=self.KEYFILE,
                        cert_file=self.CERTFILE, ca_certs=self.CACERT)
        s = xmlrpclib.ServerProxy('https://%s:%s' % (self.host, self.port),
                                  transport=vtransport)
        print s.add(2, 3)

if __name__ == '__main__':
    import sys
    if len(sys.argv) == 1:
        __Test().client()
    else:
        __Test().server()
