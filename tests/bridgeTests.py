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
import sys

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
                  secure=False, options=''):
        if options == 'port=15':
            return {'status': {'code': 0, 'message': 'Done'},
                    'power': 'on'}
        else:
            return {'status': {'code': -1, 'message': 'Failed'}}

    def getCapabilities(self):
        return {'status': {'code': 0, 'message': 'Done'},
                'info': {'My caps': 'My capabilites'}}


def createFakeAPI():
    _newAPI = imp.new_module('API')
    _API = __import__('API', globals(), locals(), {}, -1)
    setattr(_newAPI, 'Global', Host)

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


class BridgeTests(TestCaseBase):

    def testMethodWithManyOptionalAttributes(self):
        createFakeAPI()

        from rpc import Bridge
        bridge = Bridge.DynamicBridge()

        msg = ('{"jsonrpc":"2.0","method":"Host.fenceNode","params":{"addr":"r'
               'ack05-pdu01-lab4.tlv.redhat.com","port":"","agent":"apc_snmp",'
               '"username":"emesika","password":"pass","action":"off","op'
               'tions":"port=15"},"id":"c212299f-42b5-485d-b9ba-bc9880628743"'
               '}')
        obj = json.loads(msg, 'utf-8')

        mangledMethod = obj.get("method").replace(".", "_")
        params = obj.get('params', [])

        method = getattr(bridge, mangledMethod)
        self.assertEquals(method(**params), 'on')

    def testMethodWithNoParams(self):
        createFakeAPI()

        from rpc import Bridge
        bridge = Bridge.DynamicBridge()

        msg = ('{"jsonrpc":"2.0","method":"Host.getCapabilities","params":{},"'
               'id":"505ebe58-4fd7-45c6-8195-61e3a6d1dce9"}')

        obj = json.loads(msg, 'utf-8')
        mangledMethod = obj.get("method").replace(".", "_")
        params = obj.get('params', [])
        method = getattr(bridge, mangledMethod)
        bridge.register_server_address('127.0.0.1')
        self.assertEquals(method(**params)['My caps'], 'My capabilites')
        bridge.unregister_server_address()
