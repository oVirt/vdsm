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
from . import sslutils


_USE_SSL = False
_TRUSTED_STORE_PATH = '/etc/pki/vdsm'
_ADDRESS = '0'
_PORT = 54321


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
    global _USE_SSL, _TRUSTED_STORE_PATH, _ADDRESS, _PORT
    VDSM_CONF = '/etc/vdsm/vdsm.conf'
    try:
        from .config import config
        config.read(VDSM_CONF)
        _USE_SSL = config.getboolean('vars', 'ssl')
        _TRUSTED_STORE_PATH = config.get('vars', 'trust_store_path')
        _PORT = config.getint('addresses', 'management_port')
        _ADDRESS = config.get('addresses', 'management_ip')
    except:
        pass


__guessDefaults()


def cannonizeHostPort(hostPort=None, port=_PORT):
    if hostPort is None or hostPort == '0':
        addr = _ADDRESS
        if ':' in addr:
            # __guessDefaults() might set an IPv6 address, cannonize it
            addr = '[%s]' % addr
    else:
        # hostPort is in rfc3986 'host [ ":" port ]' format
        hostPort = re.match(r'(?P<Host>.+?)(:(?P<Port>\d+))?$', hostPort)
        addr = hostPort.group('Host')
        if hostPort.group('Port'):
            port = int(hostPort.group('Port'))

    return '%s:%i' % (addr, port)


def connect(hostPort=None, useSSL=None, tsPath=None,
            TransportClass=sslutils.VerifyingSafeTransport):
    hostPort = cannonizeHostPort(hostPort)
    if useSSL is None:
        useSSL = _USE_SSL
    if tsPath is None:
        tsPath = _TRUSTED_STORE_PATH
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
    print('connecting to %s:%s ssl %s ts %s' % (
        _ADDRESS, _PORT, _USE_SSL, _TRUSTED_STORE_PATH))
    server = connect()
    print(server.getVdsCapabilities())
