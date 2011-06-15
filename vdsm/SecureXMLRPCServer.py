# Copyright 2008 Red Hat, Inc. and/or its affiliates.
# Copyright 2001 Brian Quinlan.
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

Based on this article: http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/81549

For windows users: http://webcleaner.sourceforge.net/pyOpenSSL-0.6.win32-py2.4.exe
"""

import SocketServer
import BaseHTTPServer
import SimpleXMLRPCServer

import socket
from M2Crypto import SSL

class PoliteSSLConnection(SSL.Connection):

    def gettimeout(self):
         return self.socket.gettimeout()

    def accept(self):
        """Do a simple tcp accept only"""
        sock, addr = self.socket.accept()
        return PoliteSSLConnection(self.ctx, sock), addr

    def _finish_accept_ssl(self, sock, addr):
        """Finish ssl accept (possibly on a new thread)
        Code based on accept() of M2Crypto/SSL/Connection.py 0.18-2"""
        sock.addr = addr
        sock.setup_ssl()
        sock.set_accept_state()
        # Here is the change from M2Crpyto's code.
        # make sure sock.accept_ssl() does not block if self in nonblocking.
        sock.settimeout(self.socket.gettimeout())
        # On exception, I'm being nice to my peer (possible attacker?) and
        # close his socket.
        try:
            sock.accept_ssl()
            check = getattr(self, 'postConnectionCheck', self.serverPostConnectionCheck)
            if check is not None:
                if not check(self.get_peer_cert(), sock.addr[0]):
                    raise SSL.Checker.SSLVerificationError, 'post connection check failed'
        except Exception, e:
            e.addr = addr
            sock.close()
            raise
        return sock, addr

class SecureXMLRpcRequestHandler(SimpleXMLRPCServer.SimpleXMLRPCRequestHandler):
    """Secure XML-RPC request handler class.

    It it very similar to SimpleXMLRPCRequestHandler but it uses HTTPS for transporting XML data.
    """
    wbufsize = -1

    def do_POST(self):
        """Handles the HTTPS POST request."""
        SimpleXMLRPCServer.SimpleXMLRPCRequestHandler.do_POST(self)
        # I dont understand why the inherited
        # self.connection.shutdown(SSL_SENT_SHUTDOWN) without .close()
        # makes me hang
        self.connection.shutdown(0)
        self.connection.close()

class SecureXMLRPCServer(BaseHTTPServer.HTTPServer, SimpleXMLRPCServer.SimpleXMLRPCDispatcher):
    def __init__(self, server_address, keyFile, certFile, caCert,
                 logRequests=False, timeout=None, requestHandler=SecureXMLRpcRequestHandler):
        """Secure XML-RPC server.

        It it very similar to SimpleXMLRPCServer but it uses HTTPS for transporting XML data.
        """
        self.logRequests = logRequests

        try:
            SimpleXMLRPCServer.SimpleXMLRPCDispatcher.__init__(self, False, None)
        except TypeError:
            # older versions of SimpleXMLRPCServer had a different API
            SimpleXMLRPCServer.SimpleXMLRPCDispatcher.__init__(self)

        SocketServer.BaseServer.__init__(self, server_address, requestHandler)
        ctx = SSL.Context()

        ctx.load_cert_chain(certFile, keyFile)

        ctx.set_client_CA_list_from_file(caCert)
        ctx.load_verify_info(caCert)

        ctx.set_verify(SSL.verify_peer | SSL.verify_fail_if_no_peer_cert, 10)
        ctx.set_session_id_ctx ('vdsm-ssl')
        self.socket = PoliteSSLConnection(ctx, socket.socket(
                                self.address_family, self.socket_type))
        self.socket.settimeout(timeout)
        self.server_bind()
        self.server_activate()

    def finish_request(self, request, client_address):
        """Finish one request by doing ssl handshake and instantiating RequestHandlerClass."""
        request, client_address = self.socket._finish_accept_ssl(request, client_address)
        self.RequestHandlerClass(request, client_address, self)

class SecureThreadedXMLRPCServer(SocketServer.ThreadingMixIn, SecureXMLRPCServer):
    pass

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
            def __init__(self):
                import string
                self.python_string = string

            def add(self, x, y):
                return x + y

            def mult(self,x,y):
                return x*y

            def div(self,x,y):
                return x//y

            def wait(self):
                import time
                time.sleep(10)
                return 1

        server = SecureThreadedXMLRPCServer((self.host, self.port), self.KEYFILE, self.CERTFILE, self.CACERT)
        server.register_instance(xmlrpc_registers())
        print "Serving HTTPS on", self.host, "port", self.port
        server.serve_forever()

    def client(self):
        from M2Crypto.m2xmlrpclib import Server, SSL_Transport
        from M2Crypto import SSL

        ctx = SSL.Context()

        ctx.set_verify(SSL.verify_peer | SSL.verify_fail_if_no_peer_cert, 16)
        ctx.load_verify_locations(self.CACERT)
        ctx.load_cert(self.CERTFILE, self.KEYFILE)

        s = Server('https://%s:%s' % (self.host, self.port), SSL_Transport(ctx))
        print s.add(2, 3)

if __name__ == '__main__':
    import sys
    if len(sys.argv) == 1:
        __Test().client()
    else:
        __Test().server()
