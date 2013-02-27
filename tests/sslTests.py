#
# Copyright 2012 Red Hat, Inc.
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
import re
import socket
import tempfile
import threading
import subprocess
import errno

import testrunner
from vdsm import SecureXMLRPCServer


class SSLServerThread(threading.Thread):
    """A very simple server thread.

    This server waits for SSL connections in a serial
    fashion and then echoes whatever the client sends.
    """

    def __init__(self, server):
        threading.Thread.__init__(self)
        self.server = server
        self.stop = threading.Event()

    def run(self):
        # It is important to set a timeout in the server thread to be
        # able to check periodically the stop flag:
        self.server.settimeout(1)

        # Accept client connections:
        while not self.stop.isSet():
            try:
                client, address = self.server.accept()
                client.settimeout(1)
                try:
                    while True:
                        data = client.recv(1024)
                        if data:
                            client.sendall(data)
                        else:
                            break
                except:
                    # We don't care about exceptions here, only on the
                    # client side:
                    pass
                finally:
                    client.close()
            except:
                # Nothing to do here, we will check the stop flag in the
                # next iteration of the loop:
                pass

    def shutdown(self):
        # Note that this doesn't stop the thready inmediately, it just
        # indicates that stopping is requested, the thread will stop
        # with next iteration of the accept loop:
        self.stop.set()


class SSLTests(testrunner.VdsmTestCase):
    """Tests of SSL communication"""

    def setUp(self):
        """Prepares to run the tests.

        The preparation consist on creating temporary files containing
        the keys and certificates and starting a thread that runs a
        simple SSL server.
        """

        # Save the key to a file:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(KEY)
            self.keyfile = tmp.name

        # Save the certificate to a file:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(CERTIFICATE)
            self.certfile = tmp.name

        # Create the server socket:
        self.server = socket.socket()
        self.server = SecureXMLRPCServer.SSLServerSocket(
            raw=self.server,
            keyfile=self.keyfile,
            certfile=self.certfile,
            ca_certs=self.certfile)
        self.address = self.tryBind(ADDRESS)
        self.server.listen(5)

        # Start the server thread:
        self.thread = SSLServerThread(self.server)
        self.thread.start()

    def tryBind(self, address):
        ipadd, port = address
        while True:
            try:
                self.server.bind((ipadd, port))
                return (ipadd, port)
            except socket.error as ex:
                if ex.errno == errno.EADDRINUSE:
                    port += 1
                    if port > 65535:
                        raise socket.error(
                            errno.EADDRINUSE,
                            "Can not find available port to bind")
                else:
                    raise

    def tearDown(self):
        """Release the resources used by the tests.

        Removes the temporary files containing the keys and certifites,
        stops the server thread and closes the server socket.
        """

        # Delete the temporary files:
        os.remove(self.keyfile)
        os.remove(self.certfile)

        # Stop the server thread and wait for it to finish:
        self.thread.shutdown()
        self.thread.join()
        del self.thread

        # Close the server socket:
        self.server.shutdown(socket.SHUT_RDWR)
        self.server.close()
        del self.server

    def runSClient(self, args=None, input=None):
        """This method runs the OpenSSL s_client command.

        The address parameter is a tuple containg the address
        of the host and the port number that will be used to
        build the -connect option of the command.

        The args parameter is the list of additional parameters
        to pass to the command.

        The input parameter is the data that will be piped to the
        standard input of the command.

        The method returns a tuple containing the exit code of the
        command and the data generated in the standard output.
        """

        command = [
            "openssl",
            "s_client",
            "-connect", "%s:%d" % self.address,
        ]
        if args:
            command += args
        print("command=%s" % command)
        process = subprocess.Popen(command,
                                   stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        out, err = process.communicate(input)
        rc = process.wait()
        print("rc=%d" % rc)
        print("out=%s" % out)
        print("err=%s" % err)
        return rc, out

    def extractField(self, name, text):
        """
        Extracts the value of one of the informative fields provided in
        the output of the s_client command.

        The name parameter is the name of the field, for example
        Session-ID for the SSL session identifier.

        The text parameter should be the output of the execution of the
        s_client command.

        Returns the value of the given field or None if that field can't
        be fond in the provided output of the s_client command.
        """

        pattern = r"^\s*%s\s*:\s*(?P<value>[^\s]*)\s*$" % name
        expression = re.compile(pattern, flags=re.MULTILINE)
        match = expression.search(text)
        if not match:
            return None
        value = match.group("value")
        print("%s=%s" % (name, value))
        return value

    def testConnectWithoutCertificateFails(self):
        """
        Verify that the connection without a client certificate
        fails.
        """

        rc, _ = self.runSClient()
        self.assertNotEquals(rc, 0)

    def testConnectWithCertificateSucceeds(self):
        """
        Verify that the connection with a valid client certificate
        works correctly.
        """

        rc, _ = self.runSClient([
            "-cert", self.certfile,
            "-key", self.keyfile,
        ])
        self.assertEquals(rc, 0)

    def testSessionIsCached(self):
        """
        Verify that SSL the session identifier is preserved when
        connecting two times without stopping the server.
        """

        # Create a temporary file to store the session details:
        sessionDetailsFile = tempfile.NamedTemporaryFile(delete=False)

        # Connect first time and save the session to a file:
        rc, out = self.runSClient([
            "-cert", self.certfile,
            "-key", self.keyfile,
            "-sess_out", sessionDetailsFile.name,
        ])
        self.assertEquals(rc, 0)

        # Get the session id from the output of the command:
        firstSessionId = self.extractField("Session-ID", out)
        self.assertTrue(firstSessionId is not None)

        # Connect second time using the saved session file:
        rc, out = self.runSClient([
            "-cert", self.certfile,
            "-key", self.keyfile,
            "-sess_in", sessionDetailsFile.name,
        ])
        self.assertEquals(rc, 0)

        # Get the session id again:
        secondSessionId = self.extractField("Session-ID", out)
        self.assertTrue(secondSessionId is not None)

        # Remove the temporary file used to store the session details,
        # as we don't need it any longer:
        os.remove(sessionDetailsFile.name)

        # Compare the session ids:
        self.assertEquals(secondSessionId, firstSessionId)


# The address of the tests server:
ADDRESS = ("127.0.0.1", 8443)


# Private key used for the tests:
KEY = """
-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDapPcHwCWYsfiH
pJ/tXpcSZsa6ocJZaL3HF/mFxiO4/7za6lP0Vdtln4CwCzqAfUJKQhCHNyYUvZsf
Eylr0U30MQzhynq8+F5co5f2RNzz93aL7cjEUQMK2YaShLxz7o/QdoNSnT8sJ3TO
P16VEcpngoBD/nDXxNf0HekwhENYz4K2Hqol0xcGY6x8cJoXNybBPheVGTl6wy+r
W9YPuL0gR2/GgyVT1UP0EBGebkvza+eVaenrp0qrMiEQMDAOeNq3mu6ueOUo03Hn
xaEqxrToYv0eBbpF2Z469uJXaLP/NmcT1GUbFqP3H+/Js68HwxCEqb1kKGiG8E58
hSHHM95ZAgMBAAECggEAeMU2TmmsWrOze/lK/WqKN/fdPamsGIbqjNaJVYMkqENa
pfFZflUOYwu/oX4SSnbl7u6fApFLz5kL3hZPguaSEJgnbXDSax8lwDX88mMHSRsf
uBsYEphM/ek5lCUNk1vqxFMyJqgFBPamZmZKcDzreFF1WBlra0OnpYgADnSAXsT7
HcQDkSe1s1YuuRYYUuRc5KYhrQ5P3AHCJ++w7QK7wZbo/5iQuVuuytMBbCWFNH06
K+fEqZRB9wXg9ubvvbcAlX579QL2HRZl5GvhSP+2Jah/zoTndXAKVVWWx8L1ohKg
aAOxWGFy4f47BQwmkafZVYIGsfudEK4Dmf6UmwvVIQKBgQDw8r5ihTHuXLuyBtwy
J+Pn//zY1FKJcANshvFgQtrfbmLiulXDtvaiitdkQj8HyTeEtgtuGt5mnE5uKm8N
MV9eSU2FyuyazwlemI4XYdQWtcw+ZBh7K3u6/QjqDJfNjVDnv7S2VS9DDs8Ga7r4
fanecGfQ6ni5Mqxb2OAlOcBYRwKBgQDoTYmR35Lo/qkJ6Mm+8IljdvN3iAgqkO67
b6WhjkTwgO/Y+zGfQ/W2PbPsVWc1f3IBYvKmArvMDB5PZ9HyzIg27OxCyhjbLmvb
kEPjQF6f+FOb4h4yo9i2dBJucFAKrHMHiqH24Hlf3WOordxX9lY37M0fwpg2kZIM
ConIt/4EXwKBgDIXtV8UI+pTWy5K4NKImogsHywREEvEfuG8OEhz/b7/2w0aAiSb
UDFAvkD4yNPckG9FzaCJc31Pt7qNleLfRd17TeOn6YLR0jfZbYkM7KQADcNW2gQZ
aTLZ0lWeYpz4aT6VC4Pwt8+wL3g9Q3TP41X8dojnhkuybkT2FLuIgyWXAoGAMJUW
skU5qjSoEYR3vND9Sqnz3Qm7+3r4EocU8qaYUFwGzTArfo1t88EPwdtSjGOs6hFR
gdqMf+4A4MZrqAWSbzo5ZvZxIFWjBPY03G/32ijLA4zUl+6gQfggaqxecP0DyY36
tXDYsW3Ri9Ngg5znByck9wFxZ+glzRLfIfUo0K0CgYEAkogcGLKGb5zdwAXuUVQK
ftftLEARqs/gMA1cItxurtho0JUxYaaKgSICB7MQPEuTtdUNqCkeu9S838dbyfL7
gGdsZ26Can3IAyQv7+3DObvB376T4LD8Mp/ZHvOpeZQQ9O4ngadteRcBaCcd78Ij
VSgxeSvBewtCS1FnILwgXJ4=
-----END PRIVATE KEY-----
"""


# This is the certificate used for the tests, and it expires in Sep 26
# 2022, so don't be surprised if by that date the test starts failing:
CERTIFICATE = """
-----BEGIN CERTIFICATE-----
MIIC8zCCAdugAwIBAgIBADANBgkqhkiG9w0BAQUFADAUMRIwEAYDVQQDDAkxMjcu
MC4wLjEwHhcNMTIwOTI4MTcyMzE3WhcNMjIwOTI2MTcyMzE3WjAUMRIwEAYDVQQD
DAkxMjcuMC4wLjEwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDapPcH
wCWYsfiHpJ/tXpcSZsa6ocJZaL3HF/mFxiO4/7za6lP0Vdtln4CwCzqAfUJKQhCH
NyYUvZsfEylr0U30MQzhynq8+F5co5f2RNzz93aL7cjEUQMK2YaShLxz7o/QdoNS
nT8sJ3TOP16VEcpngoBD/nDXxNf0HekwhENYz4K2Hqol0xcGY6x8cJoXNybBPheV
GTl6wy+rW9YPuL0gR2/GgyVT1UP0EBGebkvza+eVaenrp0qrMiEQMDAOeNq3mu6u
eOUo03HnxaEqxrToYv0eBbpF2Z469uJXaLP/NmcT1GUbFqP3H+/Js68HwxCEqb1k
KGiG8E58hSHHM95ZAgMBAAGjUDBOMB0GA1UdDgQWBBR0dTG068xPsrXKDD6r6Ne+
8RQghzAfBgNVHSMEGDAWgBR0dTG068xPsrXKDD6r6Ne+8RQghzAMBgNVHRMEBTAD
AQH/MA0GCSqGSIb3DQEBBQUAA4IBAQCoY1bFkafDv3HIS5rBycVL0ghQV2ZgQzAj
sCZ47mgUVZKL9DiujRUFtzrMRhBBfyeT0Bv8zq+eijhGmjp8WqyRWDIwHoQwxHmD
EoQhAMR6pXvjZdYI/vwHJK5u0hADQZJ+zZp77m/p95Ds03l/g/FZHbCdISTTJnXw
t6oeDZzz/dQSAiuyAa6+0tdu2GNF8OkR5c7W+XmL797soiT1uYMgwIYQjM1NFkKN
vGc0b16ODiPvsB0bo+USw2M0grjsJEC0dN/GBgpFHO4oKAodvEWGGxANSHAXoD0E
bh5L7zBhjgag+o+ol2PDNZMrJlFvw8xzhQyvofx2h7H+mW0Uv6Yr
-----END CERTIFICATE-----
"""
