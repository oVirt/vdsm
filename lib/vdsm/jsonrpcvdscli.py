#
# Copyright 2016-2017 Red Hat, Inc.
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

from functools import partial
from uuid import uuid4

import six
from yajsonrpc import stompclient
from yajsonrpc import \
    JsonRpcRequest, \
    CALL_TIMEOUT
from yajsonrpc.exception import JsonRpcNoResponseError

from vdsm.api import vdsmapi
from vdsm.common import response
from .config import config
from . import sslutils


#############################################################################
#                                                                           #
# !!! This module is deprecated, do not add any new command converters. !!! #
#                                                                           #
#############################################################################

_COMMAND_CONVERTER = {
    'activateStorageDomain': 'StorageDomain.activate',
    'attachStorageDomain': 'StorageDomain.attach',
    'connectStoragePool': 'StoragePool.connect',
    'connectStorageServer': 'StoragePool.connectStorageServer',
    'cont': 'VM.cont',
    'clearTask': 'Task.clear',
    'create': 'VM.create',
    'createStorageDomain': 'StorageDomain.create',
    'createStoragePool': 'StoragePool.create',
    'createVG': 'LVMVolumeGroup.create',
    'createVolume': 'Volume.create',
    'destroy': 'VM.destroy',
    'destroyStoragePool': 'StoragePool.destroy',
    'detachStorageDomain': 'StorageDomain.detach',
    'disconnectStoragePool': 'StoragePool.disconnect',
    'disconnectStorageServer': 'StoragePool.disconnectStorageServer',
    'discoverSendTargets': 'ISCSIConnection.discoverSendTargets',
    'extendVolumeSize': 'Volume.extendSize',
    'formatStorageDomain': 'StorageDomain.format',
    'fullList': 'Host.getVMFullList',
    'getAllTasksInfo': 'Host.getAllTasksInfo',
    'getAllTasksStatuses': 'Host.getAllTasksStatuses',
    'getAllVmStats': 'Host.getAllVmStats',
    'getAllVmIoTunePolicies': 'Host.getAllVmIoTunePolicies',
    'getConnectedStoragePoolsList': 'Host.getConnectedStoragePools',
    'getDeviceList': 'Host.getDeviceList',
    'getImagesList': 'StorageDomain.getImages',
    'getIoTunePolicy': 'VM.getIoTunePolicy',
    'getIoTune': 'VM.getIoTune',
    'getSpmStatus': 'StoragePool.getSpmStatus',
    'getStorageDomainInfo': 'StorageDomain.getInfo',
    'getStorageDomainsList': 'Host.getStorageDomains',
    'getStorageDomainStats': 'StorageDomain.getStats',
    'getStoragePoolInfo': 'StoragePool.getInfo',
    'getTaskInfo': 'Task.getInfo',
    'getTaskStatus': 'Task.getStatus',
    'getVdsCapabilities': 'Host.getCapabilities',
    'getVdsHardwareInfo': 'Host.getHardwareInfo',
    'getVdsStats': 'Host.getStats',
    'getVGInfo': 'LVMVolumeGroup.getInfo',
    'getVolumeInfo': 'Volume.getInfo',
    'getVmStats': 'VM.getStats',
    'getVolumeSize': 'Volume.getSize',
    'getVolumesList': 'StorageDomain.getVolumes',
    'glusterTasksList': 'GlusterTask.list',
    'glusterVolumeCreate': 'GlusterVolume.create',
    'glusterVolumeSet': 'GlusterVolume.set',
    'glusterVolumesList': 'GlusterVolume.list',
    'glusterVolumeStart': 'GlusterVolume.start',
    'glusterTasksList': 'GlusterTask.list',
    'hotplugDisk': 'VM.hotplugDisk',
    'hotplugNic': 'VM.hotplugNic',
    'hotunplugDisk': 'VM.hotunplugDisk',
    'hotunplugNic': 'VM.hotunplugNic',
    'hotplugMemory': 'VM.hotplugMemory',
    'list': 'Host.getVMList',
    'migrate': 'VM.migrate',
    'migrateStatus': 'VM.getMigrationStatus',
    'migrationCreate': 'VM.migrationCreate',
    'ping': 'Host.ping',
    'prepareImage': 'Image.prepare',
    'repoStats': 'Host.getStorageRepoStats',
    'reconstructMaster': 'StoragePool.reconstructMaster',
    'setBalloonTarget': 'VM.setBalloonTarget',
    'setCpuTunePeriod': 'VM.setCpuTunePeriod',
    'setCpuTuneQuota': 'VM.setCpuTuneQuota',
    'setNumberOfCpus': 'VM.setNumberOfCpus',
    'setKsmTune': 'Host.setKsmTune',
    'setHaMaintenanceMode': 'Host.setHaMaintenanceMode',
    'setMOMPolicy': 'Host.setMOMPolicy',
    'setSafeNetworkConfig': 'Host.setSafeNetworkConfig',
    'setupNetworks': 'Host.setupNetworks',
    'setVmTicket': 'VM.setTicket',
    'setIoTune': 'VM.setIoTune',
    'setVolumeDescription': 'Volume.setDescription',
    'shutdown': 'VM.shutdown',
    'spmStart': 'StoragePool.spmStart',
    'spmStop': 'StoragePool.spmStop',
    'startMonitoringDomain': 'Host.startMonitoringDomain',
    'stopMonitoringDomain': 'Host.stopMonitoringDomain',
    'updateVmPolicy': 'VM.updateVmPolicy',
    'validateStorageDomain': 'StorageDomain.validate',
    'refresh_disk': 'VM.refresh_disk',
}


class _Server(object):

    def __init__(self, client, xml_compat):
        api_strict_mode = config.getboolean('devel', 'api_strict_mode')
        self._schema = vdsmapi.Schema.vdsm_api(api_strict_mode)
        self._client = client
        self._xml_compat = xml_compat
        self._default_timeout = CALL_TIMEOUT
        self._timeouts = {
            'migrationCreate': config.getint(
                'vars', 'migration_create_timeout'),
        }

    def set_default_timeout(self, timeout):
        self._default_timeout = timeout

    def _prepare_args(self, className, methodName, args, kwargs):
        allargs = self._schema.get_arg_names(vdsmapi.MethodRep(className,
                                                               methodName))
        params = dict(zip(allargs, args))
        params.update(kwargs)
        return params

    def _callMethod(self, methodName, *args, **kwargs):
        try:
            method = _COMMAND_CONVERTER[methodName]
        except KeyError as e:
            raise Exception("Attempt to call function: %s with "
                            "arguments: %s error: %s" %
                            (methodName, args, e))

        class_name, method_name = method.split('.')
        timeout = kwargs.pop('_transport_timeout', self._default_timeout)
        params = self._prepare_args(class_name, method_name, args, kwargs)

        req = JsonRpcRequest(method, params, reqId=str(uuid4()))

        responses = self._client.call(
            req, timeout=self._timeouts.get(method_name, timeout))
        if responses:
            resp = responses[0]
        else:
            raise JsonRpcNoResponseError(method=method)

        if resp.error is not None:
            return response.error_raw(resp.error.code, str(resp.error))

        if not self._xml_compat:
            return response.success_raw(resp.result)

        if resp.result and resp.result is not True:
            # None is translated to True inside our JSONRPC implementation
            if isinstance(resp.result, list):
                return response.success(items=resp.result)
            elif isinstance(resp.result, six.string_types):
                return response.success(resp.result)
            else:
                return response.success(**resp.result)

        return response.success()

    def migrationCreate(self, params, incomingLimit=None):
        args = [params]
        if incomingLimit is not None:
            args.append(incomingLimit)
        return self._callMethod('migrationCreate',
                                params['vmId'],
                                *args)

    def create(self, params):
        return self._callMethod('create',
                                params['vmId'],
                                params)

    def __getattr__(self, methodName):
        return partial(self._callMethod, methodName)

    def close(self):
        self._client.close()

    def __del__(self):
        self._client.close()


def _create(requestQueue,
            host=None, port=None,
            useSSL=None,
            responseQueue=None):
    if host is None:
        host = 'localhost'
    if port is None:
        port = int(config.getint('addresses', 'management_port'))

    if useSSL is None:
        useSSL = config.getboolean('vars', 'ssl')

    if useSSL:
        sslctx = sslutils.create_ssl_context()
    else:
        sslctx = None

    if responseQueue is None:
        responseQueue = str(uuid4())

    return stompclient.StandAloneRpcClient(
        host, port, requestQueue, responseQueue, sslctx,
        lazy_start=False)


def connect(requestQueue=None, stompClient=None,
            host=None, port=None,
            useSSL=None,
            responseQueue=None, xml_compat=True):
    if not requestQueue:
        request_queues = config.get("addresses", "request_queues")
        requestQueue = request_queues.split(",")[0]

    if not stompClient:
        client = _create(requestQueue,
                         host, port, useSSL,
                         responseQueue)
    else:
        client = stompclient.StompRpcClient(
            stompClient,
            requestQueue,
            str(uuid4())
        )

    return _Server(client, xml_compat)
