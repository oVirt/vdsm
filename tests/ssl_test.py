#
# Copyright 2012-2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import socket

import pytest

from vdsm.common import cmdutils
from vdsm.common import concurrent
from vdsm.common import commands
from vdsm.protocoldetector import MultiProtocolAcceptor
from vdsm.sslutils import SSLContext, SSLHandshakeDispatcher
from yajsonrpc.betterAsyncore import Reactor

from integration.sslhelper import key_cert_pair  # noqa: F401


@pytest.fixture
def fake_gethostbyaddr(monkeypatch, request):
    entry = getattr(request, 'param', None)
    if entry is not None:
        hostname, ipaddrlist = entry

        def impl(addr):
            if addr not in ipaddrlist:
                raise socket.herror()
            return (hostname, [], ipaddrlist)

        monkeypatch.setattr('vdsm.sslutils.socket.gethostbyaddr', impl)


@pytest.mark.parametrize('fake_gethostbyaddr', [('example.com', ['10.0.0.1'])],
                         indirect=True)
def test_same_string(fake_gethostbyaddr):
    assert SSLHandshakeDispatcher.compare_names('10.0.0.1', 'example.com')


@pytest.mark.parametrize('lhs,rhs', [('::ffff:127.0.0.1', '127.0.0.1'),
                                     ('127.0.0.1', '::ffff:127.0.0.1')])
def test_mapped_address(lhs, rhs):
    assert SSLHandshakeDispatcher.compare_names(lhs, rhs)


@pytest.mark.parametrize('fake_gethostbyaddr', [('example.com', ['10.0.0.1'])],
                         indirect=True)
def test_failed_mapped_address(fake_gethostbyaddr):
    assert not SSLHandshakeDispatcher.compare_names('10.0.0.1',
                                                    '::ffff:127.0.0.1')


@pytest.mark.parametrize('fake_gethostbyaddr',
                         [('example.com', ['10.0.0.1', '10.0.0.2'])],
                         indirect=True)
def test_multiple(fake_gethostbyaddr):
    assert SSLHandshakeDispatcher.compare_names('10.0.0.2', 'example.com')


@pytest.mark.parametrize('fake_gethostbyaddr',
                         [('evil.imposter.com', ['10.0.0.1'])],
                         indirect=True)
def test_imposter(fake_gethostbyaddr):
    assert not SSLHandshakeDispatcher.compare_names('10.0.0.1', 'example.com')


@pytest.mark.parametrize('lhs,rhs', [('127.0.0.1', 'example.com'),
                                     ('::1', 'example.com'),
                                     ('::ffff:127.0.0.1', 'example.com')])
def test_local_addresses(lhs, rhs):
    assert SSLHandshakeDispatcher.compare_names(lhs, rhs)


@pytest.fixture
def dummy_register_protocol_detector(monkeypatch):
    monkeypatch.setattr(MultiProtocolAcceptor, '_register_protocol_detector',
                        lambda d: d.close())


@pytest.fixture  # noqa: F811 # TODO: remove after upgrading flake to 3.9.2
def listener(dummy_register_protocol_detector, key_cert_pair, request):  # noqa: F811, E501
    key_file, cert_file = key_cert_pair
    reactor = Reactor()

    sslctx = SSLContext(cert_file=cert_file, key_file=key_file,
                        ca_certs=cert_file)

    acceptor = MultiProtocolAcceptor(
        reactor,
        '127.0.0.1',
        0,
        sslctx=sslctx
    )

    try:
        t = concurrent.thread(reactor.process_requests)
        t.start()
        (host, port) = acceptor._acceptor.socket.getsockname()[0:2]
        yield (host, port)
    finally:
        acceptor.stop()
        reactor.stop()
        t.join()


@pytest.fixture  # noqa: F811 # TODO: remove after upgrading flake to 3.9.2
def client_cmd(listener, key_cert_pair):  # noqa: F811
    key_file, cert_file = key_cert_pair

    def wrapper(protocol):
        (host, port) = listener
        cmd = ['openssl', 's_client', '-connect', '%s:%s' % (host, port),
               '-CAfile', cert_file, '-cert', cert_file, '-key', key_file,
               protocol]
        return commands.run(cmd)

    return wrapper


@pytest.mark.parametrize('protocol', [
    pytest.param(
        '-ssl2',
        id='ssl2'
    ),
    pytest.param(
        '-ssl3',
        id='ssl3'
    ),
    pytest.param(
        '-tls1',
        id='tls1'
    ),
    pytest.param(
        '-tls1_1',
        id='tls1.1'
    )
])
def test_tls_unsupported_protocols(client_cmd, protocol):
    with pytest.raises(cmdutils.Error):
        client_cmd(protocol)


@pytest.mark.parametrize('protocol', [
    pytest.param(
        '-tls1_2',
        id='tls1.2'
    ),
])
def test_tls_protocols(client_cmd, protocol):
    assert b"Verify return code: 0 (ok)" in client_cmd(protocol)
