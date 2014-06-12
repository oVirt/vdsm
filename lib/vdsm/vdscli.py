# vdscli: contact vdsm running on localhost over xmlrpc easily
#
# Copyright 2009-2014 Red Hat, Inc.
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

import xmlrpclib
import os
import re
import sys
from xml.parsers.expat import ExpatError
from . import SecureXMLRPCServer


d_useSSL = False
d_tsPath = '/etc/pki/vdsm'
d_addr = '0'
d_port = '54321'


def wrap_transport(transport):
    old_parse_response = transport.parse_response

    def wrapped_parse_response(*args, **kwargs):
        try:
            return old_parse_response(*args, **kwargs)
        except ExpatError:
            sys.stderr.write('Parsing error was thrown during parsing '
                             'response when provided: {}'.format(args[1]))
            raise
    transport.parse_response = wrapped_parse_response
    return transport


class SingleRequestTransport(xmlrpclib.Transport):
    '''Python 2.7 Transport introduced a change that makes it reuse connections
    by default when new connections are requested for a host with an existing
    connection. This class reverts the change to avoid the concurrency
    issues.'''

    def make_connection(self, host):
        '''Creates a new HTTPConnection to the host.'''
        self._connection = None
        return xmlrpclib.Transport.make_connection(self, host)


def __guessDefaults():
    global d_useSSL, d_tsPath, d_addr, d_port
    VDSM_CONF = '/etc/vdsm/vdsm.conf'
    try:
        from .config import config
        config.read(VDSM_CONF)
        d_useSSL = config.getboolean('vars', 'ssl')
        d_tsPath = config.get('vars', 'trust_store_path')
        d_port = config.get('addresses', 'management_port')
        d_addr = config.get('addresses', 'management_ip')
    except:
        pass


__guessDefaults()


def cannonizeHostPort(hostPort=None, port=d_port):
    if hostPort is None or hostPort == '0':
        addr = d_addr
        if ':' in addr:
            # __guessDefaults() might set an IPv6 address, cannonize it
            addr = '[%s]' % addr
    else:
        # hostPort is in rfc3986 'host [ ":" port ]' format
        hostPort = re.match(r'(?P<Host>.+?)(:(?P<Port>\d+))?$', hostPort)
        addr = hostPort.group('Host')
        port = hostPort.group('Port') or port

    return addr + ':' + port


def connect(hostPort=None, useSSL=None, tsPath=None,
            TransportClass=SecureXMLRPCServer.VerifyingSafeTransport):
    hostPort = cannonizeHostPort(hostPort)
    if useSSL is None:
        useSSL = d_useSSL
    if tsPath is None:
        tsPath = d_tsPath
    if useSSL:
        KEYFILE = tsPath + '/keys/vdsmkey.pem'
        CERTFILE = tsPath + '/certs/vdsmcert.pem'
        CACERT = tsPath + '/certs/cacert.pem'

        for f in (KEYFILE, CERTFILE, CACERT):
            if not os.access(f, os.R_OK):
                raise Exception("No permission to read file: %s" % f)

        transport = TransportClass(key_file=KEYFILE,
                                   cert_file=CERTFILE, ca_certs=CACERT)
        server = xmlrpclib.ServerProxy('https://%s' % hostPort,
                                       wrap_transport(transport))
    else:
        transport = wrap_transport(SingleRequestTransport())
        server = xmlrpclib.Server('http://%s' % hostPort, transport)
    return server

if __name__ == '__main__':
    print 'connecting to %s:%s ssl %s ts %s' % (d_addr, d_port,
                                                d_useSSL, d_tsPath)
    server = connect()
    print server.getVdsCapabilities()
