#
# Copyright 2012 Adam Litke, IBM Corporation
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

import logging
import os
import os.path
import socket
import errno
import json
import struct
from contextlib import closing

from testrunner import VdsmTestCase as TestCaseBase
from vdsm import constants
import BindingJsonRpc
import apiData

ip = '0.0.0.0'
port = 9824
_fakeret = {}

apiWhitelist = ('StorageDomain.Classes', 'StorageDomain.Types',
                'Volume.Formats', 'Volume.Types', 'Volume.Roles',
                'Image.DiskTypes')


def createFakeAPI():
    """
    Create a Mock API module for testing.  Mock API will return data from
    the _fakeret global variable instead of calling into vdsm.  _fakeret is
    expected to have the following format:

    {
      '<class1>': {
        '<func1>': [ <ret1>, <ret2>, ... ],
        '<func2>': [ ... ],
      }, '<class2>': {
        ...
      }
    }
    """
    class FakeObj(object):
        def __new__(cls, *args, **kwargs):
            return object.__new__(cls)

        def default(self, *args, **kwargs):
            try:
                return _fakeret[self.type][self.lastFunc].pop(0)
            except (KeyError, IndexError):
                raise Exception("No API data avilable for %s.%s" %
                                (self.type, self.lastFunc))

        def __getattr__(self, name):
            # While we are constructing the API module, use the normal getattr
            if 'API' not in sys.modules:
                return object.__getattr__(name)
            self.lastFunc = name
            return self.default

    import sys
    import imp
    from new import classobj

    _API = __import__('API', globals(), locals(), {}, -1)
    _newAPI = imp.new_module('API')

    for obj in ('Global', 'ConnectionRefs', 'StorageDomain', 'Image', 'Volume',
                'Task', 'StoragePool', 'VM'):
        cls = classobj(obj, (FakeObj,), {'type': obj})
        setattr(_newAPI, obj, cls)

    # Apply the whitelist to our version of API
    for name in apiWhitelist:
        parts = name.split('.')
        dstObj = _newAPI
        srcObj = _API
        # Walk the object hierarchy copying each component of the whitelisted
        # attribute from the real API to our fake one
        for obj in parts:
            srcObj = getattr(srcObj, obj)
            try:
                dstObj = getattr(dstObj, obj)
            except AttributeError:
                setattr(dstObj, obj, srcObj)

    # Install our fake API into the module table for use by the whole program
    sys.modules['API'] = _newAPI


def findSchema():
    """
    Find the API schema file whether we are running tests from the source dir
    or from the tests install location
    """
    scriptdir = os.path.dirname(__file__)
    localpath = os.path.join(scriptdir, '../vdsm_api/vdsmapi-schema.json')
    installedpath = os.path.join(constants.P_VDSM, 'vdsmapi-schema.json')
    for f in localpath, installedpath:
        if os.access(f, os.R_OK):
            return f
    raise Exception("Unable to find schema in %s or %s",
                    localpath, installedpath)


def setUpModule():
    """
    Set up the environment for all tests:
    1. Override the API so we can program our own return values
    2. Start an embedded server to process our requests
    """
    global port
    log = logging.getLogger('apiTests')
    handler = logging.StreamHandler()
    fmt_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(fmt_str)
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)
    log.addHandler(handler)

    schema = findSchema()
    createFakeAPI()

    # Bridge imports the API module so we must set up the fake API first
    import Bridge
    bridge = Bridge.DynamicBridge(schema)

    # Support parallel testing.  Try hard to find an open port to use
    while True:
        try:
            server = BindingJsonRpc.BindingJsonRpc(bridge, ip, port)
            break
        except socket.error as ex:
            if ex.errno == errno.EADDRINUSE:
                port += 1
                if port > 65535:
                    raise socket.error(
                        errno.EADDRINUSE,
                        "Can not find available port to bind")
            else:
                raise
    server.start()


class APITest(TestCaseBase):
    def expectAPI(self, obj, meth, retval):
        global _fakeret
        if obj not in _fakeret:
            _fakeret[obj] = {}
        if meth not in _fakeret[obj]:
            _fakeret[obj][meth] = []
        _fakeret[obj][meth].append(retval)

    def programAPI(self, key):
        key += '_apidata'
        for item in getattr(apiData, key):
            self.expectAPI(item.obj, item.meth, item.data)

    def clearAPI(self):
        global _fakeret
        _fakeret = {}


class ConnectionError(Exception):
    pass


class ProtocolError(Exception):
    pass


class JsonRawTest(APITest):
    _Size = struct.Struct("!Q")

    def buildMessage(self, data):
        msg = json.dumps(data)
        msg = msg.encode('utf-8')
        msize = JsonRawTest._Size.pack(len(msg))
        resp = msize + msg
        return resp

    def sendMessage(self, msg):
        with closing(socket.socket(socket.AF_INET,
                                   socket.SOCK_STREAM)) as sock:
            try:
                sock.connect((ip, port))
            except socket.error, e:
                raise ConnectionError("Unable to connect to server: %s", e)
            try:
                sock.sendall(msg)
            except socket.error, e:
                raise ProtocolError("Unable to send request: %s", e)
            try:
                data = sock.recv(JsonRawTest._Size.size)
            except socket.error, e:
                raise ProtocolError("Unable to read response length: %s", e)
            if not data:
                raise ProtocolError("No data received")
            msgLen = JsonRawTest._Size.unpack(data)[0]
            try:
                data = sock.recv(msgLen)
            except socket.error, e:
                raise ProtocolError("Unable to read response body: %s", e)
            if len(data) != msgLen:
                raise ProtocolError("Response body length mismatch")
            return json.loads(data)

    def testPing(self):
        self.clearAPI()
        self.programAPI("testPing")
        msg = self.buildMessage({'id': 1, 'method': 'Host.ping',
                                 'params': {}})
        reply = self.sendMessage(msg)
        self.assertFalse('error' in reply)
        self.assertEquals(None, reply['result'])

    def testPingError(self):
        self.clearAPI()
        self.programAPI("testPingError")
        msg = self.buildMessage({'id': 1, 'method': 'Host.ping',
                                 'params': {}})
        reply = self.sendMessage(msg)
        self.assertEquals(1, reply['error']['code'])
        self.assertFalse('result' in reply)

    def testNoMethod(self):
        msg = self.buildMessage({'id': 1, 'method': 'Host.fake'})
        reply = self.sendMessage(msg)
        self.assertEquals(4, reply['error']['code'])

    def testBadMethod(self):
        msg = self.buildMessage({'id': 1, 'method': 'malformed\''})
        reply = self.sendMessage(msg)
        self.assertEquals(4, reply['error']['code'])

    def testMissingSize(self):
        self.assertRaises(ProtocolError, self.sendMessage,
                          "malformed message")

    def testClientNotJson(self):
        msg = "malformed message"
        msize = JsonRawTest._Size.pack(len(msg))
        msg = msize + msg
        self.assertRaises(ProtocolError, self.sendMessage, msg)

    def testSynchronization(self):
        def doPing(msg):
            self.clearAPI()
            self.programAPI("testPing")
            ret = self.sendMessage(msg)
            self.assertFalse('error' in ret)

        msg = self.buildMessage({'id': 1, 'method': 'Host.ping'})
        # Send Truncated message
        self.assertRaises(ProtocolError, doPing, msg[:-1])

        # Test that the server recovers
        doPing(msg)

        # Send too much data
        doPing(msg + "Hello")

        # Test that the server recovers
        doPing(msg)

    def testInternalError(self):
        msg = self.buildMessage({'id': 1, 'method': 'Host.ping'})
        reply = self.sendMessage(msg)
        self.assertEquals(5, reply['error']['code'])
