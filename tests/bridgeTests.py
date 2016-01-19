#
# Copyright 2014 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
import imp
import json

from vdsm.exception import VdsmException
from rpc.Bridge import DynamicBridge
from yajsonrpc import JsonRpcError

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase as TestCaseBase

apiWhitelist = ('StorageDomain.Classes', 'StorageDomain.Types',
                'Volume.Formats', 'Volume.Types', 'Volume.Roles',
                'Image.DiskTypes', 'ConnectionRefs.ctorArgs',
                'Global.ctorArgs', 'ISCSIConnection.ctorArgs',
                'Image.ctorArgs', 'LVMVolumeGroup.ctorArgs',
                'StorageDomain.ctorArgs', 'StoragePool.ctorArgs',
                'Task.ctorArgs', 'VM.ctorArgs', 'Volume.ctorArgs')


class Host():
    ctorArgs = []

    def fenceNode(self, addr, port, agent, username, password, action,
                  secure=False, options='', policy=None):
        if options == 'port=15':
            return {'status': {'code': 0, 'message': 'Done'},
                    'power': 'on'}
        else:
            return {'status': {'code': -1, 'message': 'Failed'}}

    def getCapabilities(self):
        return {'status': {'code': 0, 'message': 'Done'},
                'info': {'My caps': 'My capabilites'}}

    def ping(self):
        raise VdsmException("Kaboom!!!")


class StorageDomain():
    ctorArgs = ['storagedomainID']

    def __init__(self, UUID):
        self._UUID = UUID

    def detach(self, spUUID, masterSdUUID=None, masterVersion=0, force=False):
        if (spUUID == '00000002-0002-0002-0002-0000000000f6' and
            masterSdUUID is None and masterVersion == 0 and
                force is not False):
            return {'status': {'code': 0, 'message': 'Done'}}
        else:
            return {'status': {'code': -1, 'message': 'Fail'}}


def getFakeAPI():
    _newAPI = imp.new_module('API')
    _API = __import__('API', globals(), locals(), {}, -1)
    setattr(_newAPI, 'Global', Host)
    setattr(_newAPI, 'StorageDomain', StorageDomain)

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
    return _newAPI


def _getApiInstance(self, className, argObj):
    className = self._convertClassName(className)

    apiObj = getattr(getFakeAPI(), className)

    ctorArgs = self._getArgs(argObj, apiObj.ctorArgs, [])
    return apiObj(*ctorArgs)


class BridgeTests(TestCaseBase):

    @MonkeyPatch(DynamicBridge, '_getApiInstance', _getApiInstance)
    def testMethodWithManyOptionalAttributes(self):
        bridge = DynamicBridge()

        msg = ('{"jsonrpc":"2.0","method":"Host.fenceNode","params":{"addr":"r'
               'ack05-pdu01-lab4.tlv.redhat.com","port":"","agent":"apc_snmp",'
               '"username":"emesika","password":"pass","action":"off","op'
               'tions":"port=15"},"id":"c212299f-42b5-485d-b9ba-bc9880628743"'
               '}')
        obj = json.loads(msg, 'utf-8')

        mangledMethod = obj.get("method").replace(".", "_")
        params = obj.get('params', [])

        method = getattr(bridge, mangledMethod)
        self.assertEquals(method(**params), {'power': 'on'})

    @MonkeyPatch(DynamicBridge, '_getApiInstance', _getApiInstance)
    def testMethodWithNoParams(self):
        bridge = DynamicBridge()

        msg = ('{"jsonrpc":"2.0","method":"Host.getCapabilities","params":{},"'
               'id":"505ebe58-4fd7-45c6-8195-61e3a6d1dce9"}')

        obj = json.loads(msg, 'utf-8')
        mangledMethod = obj.get("method").replace(".", "_")
        params = obj.get('params', [])
        method = getattr(bridge, mangledMethod)
        bridge.register_server_address('127.0.0.1')
        self.assertEquals(method(**params)['My caps'], 'My capabilites')
        bridge.unregister_server_address()

    @MonkeyPatch(DynamicBridge, '_getApiInstance', _getApiInstance)
    def testDetach(self):
        bridge = DynamicBridge()

        msg = ('{"jsonrpc":"2.0","method":"StorageDomain.detach","params":{"st'
               'oragepoolID":"00000002-0002-0002-0002-0000000000f6","force":'
               '"True","storagedomainID": "773adfc7-10d4-4e60-b700-3272ee1871'
               'f9"},"id":"505ebe58-4fd7-45c6-8195-61e3a6d1dce9"}')

        obj = json.loads(msg, 'utf-8')
        mangledMethod = obj.get("method").replace(".", "_")
        params = obj.get('params', [])
        method = getattr(bridge, mangledMethod)
        self.assertEqual(method(**params), None)

    @MonkeyPatch(DynamicBridge, '_getApiInstance', _getApiInstance)
    def testHookError(self):
        bridge = DynamicBridge()

        with self.assertRaises(JsonRpcError) as e:
            bridge.Host_ping()

        self.assertEquals(e.exception.code, 0)
