# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import importlib

import pytest
import six

from vdsm.common.exception import GeneralException, VdsmException
from vdsm.rpc.Bridge import DynamicBridge

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase as TestCaseBase

_COPIED_API_OBJECTS = (
    'Global.ctorArgs',
    'ISCSIConnection.ctorArgs',
    'Image.ctorArgs',
    'LVMVolumeGroup.ctorArgs',
    'StorageDomain.Classes',
    'StorageDomain.Types',
    'StorageDomain.ctorArgs',
    'StoragePool.ctorArgs',
    'Task.ctorArgs',
    'VM.ctorArgs',
    'Volume.Formats',
    'Volume.Roles',
    'Volume.Types',
    'Volume.ctorArgs',
)


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
        raise GeneralException("Kaboom!!!")

    def getDeviceList(self, storageType=None, guids=(), checkStatus=True,
                      refresh=True):
        if storageType != 3:
            return {'status': {'code': -1, 'message': 'Failed'}}
        if not isinstance(guids, tuple):
            return {'status': {'code': -1, 'message': 'Failed'}}
        if checkStatus:
            return {'status': {'code': -1, 'message': 'Failed'}}
        return {'status': {'code': 0, 'message': 'Done'},
                'devList': []}


class VM():
    ctorArgs = ['vmID']

    def __init__(self, UUID):
        self._UUID = UUID

    def migrationCreate(self, params, incomingLimit):
        if self._UUID == params['vmID'] and incomingLimit == 42:
            return {'status': {'code': 0, 'message': 'Done'},
                    'migrationPort': 0, 'params': {}}
        else:
            return {'status': {'code': -1, 'message': 'Fail'}}


class StorageDomain():
    ctorArgs = []

    def detach(
            self,
            storagedomainID,
            spUUID,
            masterSdUUID=None,
            masterVersion=0,
            force=False):
        if (spUUID == '00000002-0002-0002-0002-0000000000f6' and
            masterSdUUID is None and masterVersion == 0 and
                force is not False):
            return {'status': {'code': 0, 'message': 'Done'}}
        else:
            return {'status': {'code': -1, 'message': 'Fail'}}


def getFakeAPI():
    spec = importlib.machinery.ModuleSpec("vdsm.API", None)
    _newAPI = importlib.util.module_from_spec(spec)
    _vdsm = __import__('vdsm', globals(), locals())
    _API = _vdsm.API
    setattr(_newAPI, 'Global', Host)
    setattr(_newAPI, 'StorageDomain', StorageDomain)
    setattr(_newAPI, 'VM', VM)

    # Copy required API objects to our version of API
    for name in _COPIED_API_OBJECTS:
        parts = name.split('.')
        dstObj = _newAPI
        srcObj = _API
        # Walk the object hierarchy copying each component of the
        # _COPIED_API_OBJECTS attribute from the real API to our fake one
        for obj in parts:
            srcObj = getattr(srcObj, obj)
            try:
                dstObj = getattr(dstObj, obj)
            except AttributeError:
                setattr(dstObj, obj, srcObj)
    return _newAPI


def _get_api_instance(self, className, argObj):
    className = self._convert_class_name(className)

    apiObj = getattr(getFakeAPI(), className)

    ctorArgs = self._get_args(argObj, apiObj.ctorArgs, [], [])
    return apiObj(*ctorArgs)


@pytest.mark.xfail(six.PY2, reason="unsupported on py2")
class BridgeTests(TestCaseBase):

    @MonkeyPatch(DynamicBridge, '_get_api_instance', _get_api_instance)
    def testMethodWithManyOptionalAttributes(self):
        bridge = DynamicBridge()

        params = {"addr": "rack05-pdu01-lab4.tlv.redhat.com", "port": "",
                  "agent": "apc_snmp", "username": "emesika",
                  "password": "pass", "action": "off", "options": "port=15"}

        self.assertEqual(bridge.dispatch('Host.fenceNode')(**params),
                         {'power': 'on'})

    @MonkeyPatch(DynamicBridge, '_get_api_instance', _get_api_instance)
    def testMethodWithNoParams(self):
        bridge = DynamicBridge()

        bridge.register_server_address('127.0.0.1')
        self.assertEqual(bridge.dispatch('Host.getCapabilities')()
                         ['My caps'], 'My capabilites')
        bridge.unregister_server_address()

    @MonkeyPatch(DynamicBridge, '_get_api_instance', _get_api_instance)
    def testDetach(self):
        bridge = DynamicBridge()

        params = {"storagepoolID": "00000002-0002-0002-0002-0000000000f6",
                  "force": "True",
                  "storagedomainID": "773adfc7-10d4-4e60-b700-3272ee1871f9"}

        self.assertEqual(bridge.dispatch('StorageDomain.detach')(**params),
                         None)

    @MonkeyPatch(DynamicBridge, '_get_api_instance', _get_api_instance)
    def testHookError(self):
        bridge = DynamicBridge()

        with self.assertRaises(VdsmException) as e:
            bridge.dispatch('Host.ping')()

        self.assertEqual(e.exception.code, 100)

    @MonkeyPatch(DynamicBridge, '_get_api_instance', _get_api_instance)
    def testMethodWithIntParam(self):
        bridge = DynamicBridge()

        params = {"vmID": "773adfc7-10d4-4e60-b700-3272ee1871f9",
                  "params": {"vmID": "773adfc7-10d4-4e60-b700-3272ee1871f9"},
                  "incomingLimit": 42}
        self.assertEqual(bridge.dispatch('VM.migrationCreate')(**params),
                         {'migrationPort': 0, 'params': {}})

    @MonkeyPatch(DynamicBridge, '_get_api_instance', _get_api_instance)
    def testDefaultValues(self):
        bridge = DynamicBridge()

        params = {'storageType': 3, 'checkStatus': False}

        self.assertEqual(bridge.dispatch('Host.getDeviceList')(**params),
                         [])
