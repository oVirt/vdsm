#
# Copyright 2012 Zhou Zheng Sheng, IBM Corporation
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
import socket
import sys
from tempfile import mkstemp
from contextlib import contextmanager

from testlib import VdsmTestCase as TestCaseBase
from testValidation import brokentest
from monkeypatch import MonkeyPatch

from vdsm import vdscli
import vdsClient


@contextmanager
def configFile(args):
    fd, path = mkstemp()
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(args))

    try:
        yield path
    finally:
        os.unlink(path)


@contextmanager
def passFile(password):
    fd, path = mkstemp()
    with os.fdopen(fd, "w") as f:
        f.write(password)

    try:
        yield path
    finally:
        os.unlink(path)


class fakeXMLRPCServer(object):

    def __init__(self, testCase):
        self.testCase = testCase

    def create(self, params):
        return params

    def discoverSendTargets(self, params):
        return {'status':
                {'code': 1, 'message': params}}

    def connectStorageServer(self, serverType, spUUID, conList):
        return {'status':
                {'code': 1, 'message': conList}}

    def setVmTicket(self, vmId, otp64, secs, connAct, params):
        return {'status':
                {'code': 0, 'message': otp64}}

    def validateStorageServerConnection(self, domainType, spUUID,
                                        connectionParams):
        return {'status':
                {'code': 1, 'message': connectionParams}}

    def disconnectStorageServer(self, serverType, spUUID, conList):
        return {'status':
                {'code': 1, 'message': conList}}

    def desktopLogin(self, vmId, domain, user, password):
        self.testCase.assertEquals(password, 'password')
        return {'status': {'code': 0, 'message': ''}}


def fakeExecAndExit(response, parameterName=None):
    return response


def createFakeService(testCase):
    serv = vdsClient.service()
    fakeServer = fakeXMLRPCServer(testCase)
    serv.s = fakeServer
    serv.ExecAndExit = fakeExecAndExit
    return serv


class vdsClientTest(TestCaseBase):
    def testCreateArgumentParsing(self):
        serv = createFakeService(self)

        plainArgs = ['vmId=209b27e4-aed3-11e1-a547-00247edb4743', 'vmType=kvm',
                     'kvmEnable=true', 'memSize=1024',
                     'macAddr=52:54:00:7d:96:bd', 'nicModel=virtio',
                     'bridge=virbr0', 'display=vnc',
                     'cdrom=/path/to/some.iso', 'boot=c', 'vmName=rhel62vdsm',
                     'smp=2', 'acpiEnable=True']
        nestArgs = ['drive=pool:pooid,domain:domainpi,image:imageid,'
                    'volume:volumeid,boot:true,format:cow',
                    'devices={device:ide,type:controller}',
                    'devices={nicModel:virtio,macAddr:5F:45:00:95:F6:3F,'
                    'network:virbr0,alias:net0,address:{slot:0x03,bus:0x00,'
                    'domain:0x0000,type:pci,function:0x0}}',
                    'guestNumaNodes={cpus:0-1,memory:5120}',
                    'guestNumaNodes={cpus:2-3,memory:5120}',
                    'numaTune={mode:strict,nodeset:0}',
                    'cpuPinning={0:0,1:1}']
        allArgs = plainArgs + nestArgs

        expectResult = {'acpiEnable': 'True',
                        'boot': 'c',
                        'bridge': 'virbr0',
                        'cdrom': '/path/to/some.iso',
                        'cpuPinning': {'0': '0', '1': '1'},
                        'numaTune': {'mode': 'strict', 'nodeset': '0'},
                        'devices': [{'device': 'ide', 'type': 'controller'},
                                    {'address': {'bus': '0x00',
                                                 'domain': '0x0000',
                                                 'function': '0x0',
                                                 'slot': '0x03',
                                                 'type': 'pci'},
                                     'alias': 'net0',
                                     'macAddr': '5F:45:00:95:F6:3F',
                                     'network': 'virbr0',
                                     'nicModel': 'virtio'}],
                        'display': 'vnc',
                        'drives': [{'boot': 'true',
                                    'domainID': 'domainpi',
                                    'format': 'cow',
                                    'imageID': 'imageid',
                                    'poolID': 'pooid',
                                    'volumeID': 'volumeid'}],
                        'kvmEnable': 'true',
                        'macAddr': '52:54:00:7d:96:bd',
                        'memSize': '1024',
                        'nicModel': 'virtio',
                        'smp': '2',
                        'vmId': '209b27e4-aed3-11e1-a547-00247edb4743',
                        'vmName': 'rhel62vdsm',
                        'vmType': 'kvm',
                        'guestNumaNodes': [{'cpus': '0-1',
                                            'memory': '5120'},
                                           {'cpus': '2-3',
                                            'memory': '5120'}]}

        # test parsing only arguments
        r1 = serv.do_create(['/dev/null'] + allArgs)
        self.assertEquals(r1, expectResult)

        # test parsing only configure file
        with configFile(allArgs) as conf:
            r2 = serv.do_create([conf])
        self.assertEquals(r2, expectResult)

        # test parsing configure file and arguments
        with configFile(plainArgs) as conf:
            r3 = serv.do_create([conf] + nestArgs)
        self.assertEquals(r3, expectResult)

        # changing one argument should result a different dictionary
        allArgs[-1] = 'cpuPinning={0:1,1:0}'
        r4 = serv.do_create(['/dev/null'] + allArgs)
        self.assertNotEquals(r4, expectResult)

        # changing one argument should result a different dictionary
        allArgs[-2] = 'numaTune={mode:strict,nodeset:1}'
        r4 = serv.do_create(['/dev/null'] + allArgs)
        self.assertNotEquals(r4, expectResult)

    def testFileDiscoverST(self):
        serv = createFakeService(self)
        password = 'password'

        with passFile(password) as filename:
            args = ['localhost:7777', 'username', '-', 'auth=file:' + filename]
            result = serv.discoverST(args)
            self.assertEqual(result[1]['password'], password)

    @MonkeyPatch(os, 'environ', {'my_password': 'password'})
    def testEnvDiscoverST(self):
        serv = createFakeService(self)

        args = ['localhost:7777', 'username', '-', 'auth=env:my_password']
        result = serv.discoverST(args)
        self.assertEqual(result[1]['password'], 'password')

    def testOldDiscoverST(self):
        serv = createFakeService(self)
        password = 'password'

        args = ['localhost:7777', 'username', password]
        result = serv.discoverST(args)
        self.assertEqual(result[1]['password'], password)

    def testPassDiscoverST(self):
        serv = createFakeService(self)
        password = 'password'

        args = ['localhost:7777', 'username', '-', 'auth=pass:' + password]
        result = serv.discoverST(args)
        self.assertEqual(result[1]['password'], password)

    def testOldDiscoverSTExtraParams(self):
        serv = createFakeService(self)
        password = 'password'

        args = ['localhost:7777', 'username', password, 'foo=bar']
        result = serv.discoverST(args)
        self.assertEqual(result[1]['password'], password)

    def testFileConnectStorageServer(self):
        serv = createFakeService(self)
        password = 'password'

        with passFile(password) as filename:
            args = [1, '00000000-0000-0000-0000-000000000000',
                    ('id=null,connection=192.168.1.10:/export/data,'
                     'portal=null,port=2049,iqn=null,user=username,'
                     'auth=file:') + filename]
            result = serv.connectStorageServer(args)
            self.assertEqual(result[1][0]['password'], password)

    def testOldConnectStorageServer(self):
        serv = createFakeService(self)
        password = 'password'

        args = [1, '00000000-0000-0000-0000-000000000000',
                ('id=null,connection=192.168.1.10:/export/data,'
                 'portal=null,port=2049,iqn=null,user=username,'
                 'password=') + password]
        result = serv.connectStorageServer(args)
        self.assertEqual(result[1][0]['password'], password)

    @MonkeyPatch(os, 'environ', {'my_password': 'password'})
    def testEnvConnectStorageServer(self):
        serv = createFakeService(self)

        args = [1, '00000000-0000-0000-0000-000000000000',
                ('id=null,connection=192.168.1.10:/export/data,'
                 'portal=null,port=2049,iqn=null,user=username,'
                 'auth=env:my_password')]
        result = serv.connectStorageServer(args)
        self.assertEqual(result[1][0]['password'], 'password')

    def testPassConnectStorageServer(self):
        serv = createFakeService(self)
        password = 'password'

        args = [1, '00000000-0000-0000-0000-000000000000',
                ('id=null,connection=192.168.1.10:/export/data,'
                 'portal=null,port=2049,iqn=null,user=username,'
                 'auth=pass:') + password]
        result = serv.connectStorageServer(args)
        self.assertEqual(result[1][0]['password'], password)

    def testFileSetVmTicket(self):
        serv = createFakeService(self)
        password = 'password'

        with passFile(password) as filename:
            args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390',
                    '-', '120', 'keep', '--', 'auth=file:' + filename]
            result = serv.do_setVmTicket(args)
            self.assertEqual(result['status']['message'], password)

    @MonkeyPatch(os, 'environ', {'my_password': 'password'})
    def testEnvSetVmTicket(self):
        serv = createFakeService(self)

        args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390',
                '120', 'keep', '--', 'auth=env:my_password']
        result = serv.do_setVmTicket(args)
        self.assertEqual(result['status']['message'], 'password')

    def testOldSetVmTicket(self):
        serv = createFakeService(self)
        password = 'password'

        args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390', password, '120',
                'keep']
        result = serv.do_setVmTicket(args)
        self.assertEqual(result['status']['message'], password)

    def testOldSetVmTicketExtraParams(self):
        serv = createFakeService(self)
        password = 'password'

        args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390', password, '120',
                'keep', '--', 'foo=bar']
        result = serv.do_setVmTicket(args)
        self.assertEqual(result['status']['message'], password)

    def testPassSetVmTicket(self):
        serv = createFakeService(self)
        password = 'password'

        args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390',
                '-', '120', 'keep', '--', 'auth=pass:' + password]
        result = serv.do_setVmTicket(args)
        self.assertEqual(result['status']['message'], password)

    def testFailingFileSetVmTicket(self):
        serv = createFakeService(self)
        args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390', 'password', '120',
                'keep', '--', 'auth=file:/i/do/not/exist']
        with self.assertRaises(IOError):
            serv.do_setVmTicket(args)

    def testFailingNoSuchMethodSetVmTicket(self):
        serv = createFakeService(self)
        args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390', 'password', '120',
                'keep', '--', 'auth=foo:bar']
        with self.assertRaises(RuntimeError):
            serv.do_setVmTicket(args)

    def testFailingNoColonSetVmTicket(self):
        serv = createFakeService(self)
        args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390', 'password', '120',
                'keep', '--', 'auth=foobar']
        with self.assertRaises(RuntimeError):
            serv.do_setVmTicket(args)

    @MonkeyPatch(os, 'environ', {})
    def testFailingEnvSetVmTicket(self):
        serv = createFakeService(self)

        args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390', 'password', '120',
                'keep', '--', 'auth=env:NOVAR']
        with self.assertRaises(RuntimeError):
            serv.do_setVmTicket(args)

    def testFileValidateStorageServerConnection(self):
        serv = createFakeService(self)
        password = 'password'

        with passFile(password) as filename:
            args = [1, '00000000-0000-0000-0000-000000000000',
                    ('id=null,connection=192.168.1.10:/export/data,'
                     'portal=null,port=2049,iqn=null,user=username,'
                     'auth=file:') + filename]
            result = serv.validateStorageServerConnection(args)
            self.assertEqual(result[1][0]['password'], password)

    @MonkeyPatch(os, 'environ', {'my_password': 'password'})
    def testEnvValidateStorageServerConnection(self):
        serv = createFakeService(self)

        args = [1, '00000000-0000-0000-0000-000000000000',
                ('id=null,connection=192.168.1.10:/export/data,'
                 'portal=null,port=2049,iqn=null,user=username,'
                 'auth=env:my_password')]
        result = serv.validateStorageServerConnection(args)
        self.assertEqual(result[1][0]['password'], 'password')

    def testOldValidateStorageServerConnection(self):
        serv = createFakeService(self)
        password = 'password'

        args = [1, '00000000-0000-0000-0000-000000000000',
                ('id=null,connection=192.168.1.10:/export/data,'
                 'portal=null,port=2049,iqn=null,user=username,'
                 'password=') + password]
        result = serv.validateStorageServerConnection(args)
        self.assertEqual(result[1][0]['password'], password)

    def testPassAndOldValidateStorageServerConnection(self):
        serv = createFakeService(self)
        password = 'password'

        args = [1, '00000000-0000-0000-0000-000000000000',
                ('id=null,connection=192.168.1.10:/export/data,'
                 'portal=null,port=2049,iqn=null,user=username,'
                 'password=wrong_password,auth=pass:' + password)]
        result = serv.validateStorageServerConnection(args)
        self.assertEqual(result[1][0]['password'], password)

    def testPassValidateStorageServerConnection(self):
        serv = createFakeService(self)
        password = 'password'

        args = [1, '00000000-0000-0000-0000-000000000000',
                ('id=null,connection=192.168.1.10:/export/data,'
                 'portal=null,port=2049,iqn=null,user=username,'
                 'auth=pass:' + password)]
        result = serv.validateStorageServerConnection(args)
        self.assertEqual(result[1][0]['password'], password)

    def testFileDisconnectStorageServer(self):
        serv = createFakeService(self)
        password = 'password'

        with passFile(password) as filename:
            args = [1, '00000000-0000-0000-0000-000000000000',
                    ('id=null,connection=192.168.1.10:/export/data,'
                     'portal=null,port=2049,iqn=null,user=username,'
                     'auth=file:') + filename]
            result = serv.disconnectStorageServer(args)
            self.assertEqual(result[1][0]['password'], password)

    @MonkeyPatch(os, 'environ', {'my_password': 'password'})
    def testEnvDisconnectStorageServer(self):
        serv = createFakeService(self)

        args = [1, '00000000-0000-0000-0000-000000000000',
                ('id=null,connection=192.168.1.10:/export/data,'
                 'portal=null,port=2049,iqn=null,user=username,'
                 'auth=env:my_password')]
        result = serv.disconnectStorageServer(args)
        self.assertEqual(result[1][0]['password'], 'password')

    def testOldDisconnectStorageServer(self):
        serv = createFakeService(self)
        password = 'password'

        args = [1, '00000000-0000-0000-0000-000000000000',
                ('id=null,connection=192.168.1.10:/export/data,'
                 'portal=null,port=2049,iqn=null,user=username,'
                 'password=') + password]
        result = serv.disconnectStorageServer(args)
        self.assertEqual(result[1][0]['password'], password)

    def testPassDisconnectStorageServer(self):
        serv = createFakeService(self)
        password = 'password'

        args = [1, '00000000-0000-0000-0000-000000000000',
                ('id=null,connection=192.168.1.10:/export/data,'
                 'portal=null,port=2049,iqn=null,user=username,'
                 'auth=pass:') + password]
        result = serv.disconnectStorageServer(args)
        self.assertEqual(result[1][0]['password'], password)

    @MonkeyPatch(sys, 'exit', lambda *y, **x: FakeExit())
    def testFileDesktopLogin(self):
        serv = createFakeService(self)
        password = 'password'

        with passFile(password) as filename:
            args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390', 'internal',
                    'username', '-', 'auth=file:' + filename]
            serv.desktopLogin(args)

    @MonkeyPatch(os, 'environ', {'my_password': 'password'})
    @MonkeyPatch(sys, 'exit', lambda *y, **x: FakeExit())
    def testEnvDesktopLogin(self):
        serv = createFakeService(self)

        args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390', 'internal',
                'username', '-', 'auth=env:my_password']
        serv.desktopLogin(args)

    @MonkeyPatch(sys, 'exit', lambda *y, **x: FakeExit())
    def testOldDesktopLogin(self):
        serv = createFakeService(self)

        args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390', 'internal',
                'username', 'password']
        serv.desktopLogin(args)

    @MonkeyPatch(sys, 'exit', lambda *y, **x: FakeExit())
    def testOldDesktopLoginExtraParams(self):
        serv = createFakeService(self)

        args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390', 'internal',
                'username', 'password', 'foo=bar']
        serv.desktopLogin(args)

    @MonkeyPatch(sys, 'exit', lambda *y, **x: FakeExit())
    def testPassDesktopLogin(self):
        serv = createFakeService(self)

        args = ['25ec8c1f-38fa-404c-a59a-84eba1f0a390', 'internal',
                'username', '-', 'auth=pass:password']
        serv.desktopLogin(args)

    def testPlainParseArgs(self):
        fixture = "key1=val1,key2=val2,key3=val3"
        args = vdsClient.parseArgs(fixture)
        self.assertEquals(args, {'key1': 'val1', 'key2': 'val2',
                                 'key3': 'val3'})

    def testQuotedParseArgs(self):
        fixture = "key1=\"val1\",'key2'=val2,key3='val3'"
        args = vdsClient.parseArgs(fixture)
        self.assertEquals(args, {'key1': 'val1', 'key2': 'val2',
                                 'key3': 'val3'})

    def testQuotedCommasParseArgs(self):
        fixture = "key1=val1,\"k,e,y,2\"=val2,key3='v,a,l,3'"
        args = vdsClient.parseArgs(fixture)
        self.assertEquals(args, {'key1': 'val1', 'k,e,y,2': 'val2',
                                 'key3': 'v,a,l,3'})

    def testEscapedQuotesParseArgs(self):
        fixture = "k\\\'ey1=v\\\"al1,key2=\"va\\\"l2\",key3=val3"
        args = vdsClient.parseArgs(fixture)
        self.assertEquals(args, {'k\'ey1': 'v"al1', 'key2': 'va"l2',
                                 'key3': 'val3'})

    def testEscapedCommasParseArgs(self):
        fixture = "key1=val1,key2=v\\,al2,key3=val3"
        args = vdsClient.parseArgs(fixture)
        self.assertEquals(args, {'key1': 'val1', 'key2': 'v,al2',
                                 'key3': 'val3'})


class CannonizeHostPortTest(TestCaseBase):

    def testNoArguments(self):
        self._assertIsIpAddressWithPort(vdscli.cannonizeHostPort())

    def testNoneArgument(self):
        self._assertIsIpAddressWithPort(vdscli.cannonizeHostPort(None))

    def testNoneArgumentAndPort(self):
        port = 65432
        res = vdscli.cannonizeHostPort(None, port)
        self._assertIsIpAddressWithPort(res)
        # address must include the given port
        self.assertTrue(res.endswith(str(port)))

    @brokentest
    def testEmptyAddress(self):
        # TODO: fix cannonizeHostPort to handle this error or to
        # raise a more meaningful error
        self.assertRaises(ValueError,
                          vdscli.cannonizeHostPort,
                          '')

    def testAddressNoPort(self):
        self._assertIsIpAddressWithPort(
            vdscli.cannonizeHostPort('127.0.0.1'))

    def testAddressWithPort(self):
        address = "127.0.0.1:65432"
        self.assertEqual(address, vdscli.cannonizeHostPort(address))

    def testAddressWithPortParameter(self):
        addr = '127.0.0.1'
        port = 65432
        res = vdscli.cannonizeHostPort(addr, port)
        self._assertIsIpAddressWithPort(res)
        # address must include the given port
        self.assertTrue(res.endswith(str(port)))

    def testAddressWithBadPortParameter(self):
        addr = '127.0.0.1'
        port = '65432'
        self.assertRaises(TypeError,
                          vdscli.cannonizeHostPort,
                          addr, port)

    def _assertIsIpAddressWithPort(self, addrWithPort):
        try:
            # to handle IPv6, we expect the \[ipv6\][:port] notation.
            # this split also gracefully handle ipv4:port notation.
            # details: http://tools.ietf.org/html/rfc5952#page-11
            # the following will handle all IP families:
            addr, port = addrWithPort.rsplit(':', 1)
        except ValueError:
            raise AssertionError('%s is not a valid IP address:' %
                                 addrWithPort)
        else:
            self._assertValidAddress(addr)
            self._assertValidPort(port)

    def _assertValidAddress(self, addr):
        if addr.count('.'):
            if not _isIPv4Address(addr):
                raise AssertionError('invalid IPv4 address: %s',
                                     addr)
        elif addr.count(':'):
            if not addr.startswith('[') or not addr.endswith(']'):
                raise AssertionError('malformed IPv6 address: %s',
                                     addr)
            if not _isIPv6Address(addr[1:-1]):
                raise AssertionError('invalid IPv6 address: %s',
                                     addr)
        else:
            raise AssertionError('unrecognized IP address family: %s',
                                 addr)

    def _assertValidPort(self, port_str):
        try:
            port = int(port_str)
        except ValueError:
            raise AssertionError('malformed port: %s' % port_str)
        if port <= 0 or port >= 2**16:
            raise AssertionError('malformed port: %s' % port_str)


def _isIPv4Address(address):
    try:
        socket.inet_pton(socket.AF_INET, address)
    except socket.error:
        return False
    else:
        return True


def _isIPv6Address(address):
    addr = address.split('/', 1)
    try:
        socket.inet_pton(socket.AF_INET6, addr[0])
    except socket.error:
        return False
    else:
        if len(addr) == 2:
            return _isValidPrefixLen(addr[1])
        return True


def _isValidPrefixLen(prefixlen):
    try:
        prefixlen = int(prefixlen)
        if prefixlen < 0 or prefixlen > 127:
            return False
    except ValueError:
        return False
    return True


class FakeExit():
    def exit(self, code):
        pass


class _FakePopen():
    def __init__(self, output):
        self._output = output
        self.returncode = 0

    def __call__(self, *args, **kwarg):
        pass

    def communicate(self):
        return self._output, ''
