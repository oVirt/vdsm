# Copyright (C) 2012 - 2017 Adam Litke, IBM Corporation
# Copyright 2016-2018 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public
# License along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

from __future__ import absolute_import
from __future__ import division
from functools import partial

import logging
import threading
import types

from yajsonrpc import exception

from vdsm import API
from vdsm.api import vdsmapi
from vdsm.config import config
from vdsm.network.netinfo.addresses import getDeviceByIP


try:
    import vdsm.gluster.apiwrapper as gapi
    from vdsm.gluster import exception as ge
    _glusterEnabled = True
except ImportError:
    _glusterEnabled = False


class VdsmError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.msg = message

    def __str__(self):
        return '[error %d] %s' % (self.code, self.msg)


class InvalidCall(Exception):

    def __init__(self, function, arguments, error):
        self.function = function
        self.arguments = arguments
        self.error = error

    def __str__(self):
        return ("Attempt to call function: %s with arguments: %s error: %s" %
                (self.function, self.arguments, self.error))


class DynamicBridge(object):
    def __init__(self):
        api_strict_mode = config.getboolean('devel', 'api_strict_mode')
        self._schema = vdsmapi.Schema.vdsm_api(api_strict_mode,
                                               with_gluster=_glusterEnabled)

        self._event_schema = vdsmapi.Schema.vdsm_events(api_strict_mode)

        self._threadLocal = threading.local()
        self.log = logging.getLogger('DynamicBridge')

    def register_server_address(self, server_address):
        self._threadLocal.server = server_address

    @property
    def event_schema(self):
        return self._event_schema

    def unregister_server_address(self):
        self._threadLocal.server = None

    def _get_args(self, argobj, arglist, defaultArgs, defaultValues):
        ret = ()
        for arg in arglist:
            if arg in defaultArgs:
                if arg in argobj:
                    ret = ret + (argobj[arg],)
                else:
                    if len(defaultValues) > 0:
                        ret = ret + (defaultValues[0],)
                defaultValues = defaultValues[1:]
            elif arg in argobj:
                ret = ret + (argobj[arg],)
        return ret

    def _get_result(self, response, member=None):
        if member is None:
            return None
        try:
            return response[member]
        except KeyError:
            raise VdsmError(5, "Response is missing '%s' member" % member)

    def dispatch(self, method):
        try:
            className, methodName = method.split('.', 1)
            self._schema.get_method(vdsmapi.MethodRep(className, methodName))
        except (vdsmapi.MethodNotFound, ValueError):
            raise exception.JsonRpcMethodNotFoundError(method=method)
        return partial(self._dynamicMethod, className, methodName)

    def _convert_class_name(self, name):
        """
        The schema has a different name for the 'Global' namespace.  Until
        API.py is fixed, we convert the schema name if we are looking up
        something in API.py.
        """
        name_map = {'Host': 'Global'}
        try:
            return name_map[name]
        except KeyError:
            return name

    def _get_method_args(self, rep, argObj):
        """
        An internal API call currently looks like:

            instance = API.<className>(<*ctor_args>)
            intance.<method>(<*method_args>)

        Eventually we can remove this instancing but for now that's the way it
        works.  Each API.py object defines its ctor_args so that we can query
        them from here.  For any given method, the method_args are obtained by
        chopping off the ctor_args from the beginning of argObj.
        """
        allArgs = self._schema.get_arg_names(rep)

        class_name = self._convert_class_name(rep.object_name)
        if _glusterEnabled and class_name.startswith('Gluster'):
            ctorArgs = getattr(gapi, class_name).ctorArgs
        else:
            ctorArgs = getattr(API, class_name).ctorArgs

        defaultArgs = self._schema.get_default_arg_names(rep)
        defaultValues = self._schema.get_default_arg_values(rep)

        # Determine the method arguments by subtraction
        methodArgs = []
        for arg in allArgs:
            if arg not in ctorArgs:
                methodArgs.append(arg)

        return self._get_args(argObj, methodArgs, defaultArgs, defaultValues)

    def _get_api_instance(self, className, argObj):
        className = self._convert_class_name(className)

        if _glusterEnabled and className.startswith('Gluster'):
            apiObj = getattr(gapi, className)
        else:
            apiObj = getattr(API, className)

        ctorArgs = self._get_args(argObj, apiObj.ctorArgs, [], [])
        return apiObj(*ctorArgs)

    def _name_args(self, args, kwargs, arglist):
        kwargs = kwargs.copy()
        for i, arg in enumerate(args):
            argName = arglist[i]
            kwargs[argName] = arg

        return kwargs

    def _dynamicMethod(self, className, methodName, *args, **kwargs):
        rep = vdsmapi.MethodRep(className, methodName)
        argobj = self._name_args(args, kwargs, self._schema.get_arg_names(rep))

        self._schema.verify_args(rep, argobj)
        api = self._get_api_instance(className, argobj)

        methodArgs = self._get_method_args(rep, argobj)

        # Call the override function (if given).  Otherwise, just call directly
        cmd = '%s_%s' % (className, methodName)
        fn = command_info.get(cmd, {}).get('call')
        if fn:
            result = fn(api, argobj)
        else:
            fn = getattr(api, methodName)
            try:
                if _glusterEnabled:
                    try:
                        result = fn(*methodArgs)
                    except ge.GlusterException as e:
                        result = e.response()
                else:
                        result = fn(*methodArgs)
            except TypeError as e:
                self.log.exception("TypeError raised by dispatched function")
                raise InvalidCall(fn, methodArgs, e)

        if result['status']['code']:
            raise exception.JsonRpcServerError.from_dict(result['status'])

        retfield = command_info.get(cmd, {}).get('ret')
        if isinstance(retfield, types.FunctionType):
            if cmd == 'Host_getCapabilities':
                ret = retfield(self._threadLocal.server, result)
            else:
                ret = retfield(result)
        elif _glusterEnabled and className.startswith('Gluster'):
            ret = dict([(key, value) for key, value in result.items()
                        if key is not 'status'])
        else:
            ret = self._get_result(result, retfield)

        self._schema.verify_retval(vdsmapi.MethodRep(className, methodName),
                                   ret)
        return ret


def Host_fenceNode_Ret(ret):
    """
    Only 'power' and 'operationStatus' should be part of return value if they
    are not None
    """
    result = {}
    for key in ('power', 'operationStatus'):
        val = ret.get(key)
        if val is not None:
            result[key] = val
    return result


def Host_getCapabilities_Ret(server_address, ret):
    """
    We need to add additional information to getCaps as it was done for xmlrpc.
    """
    ret['info']['lastClientIface'] = getDeviceByIP(server_address)

    return ret['info']


def Host_getStorageRepoStats_Ret(ret):
    """
    The returned dictionary doesn't separate the stats from the status code
    so we need to rebuild the result.
    """
    del ret['status']
    return ret


def Host_getVMList_Call(api, args):
    """
    This call is only interested in returning the VM UUIDs so pass False for
    the first argument in order to suppress verbose results.
    """
    vmList = args.get('vmList', [])
    onlyUUID = args.get('onlyUUID', True)
    return API.Global().getVMList(False, vmList, onlyUUID)


def Host_getVMFullList_Call(api, args):
    """
    This call is interested in returning full status.
    """
    vmList = args.get('vmList', [])
    return API.Global().getVMList(True, vmList, False)


def StoragePool_getInfo_Ret(ret):
    """
    The result contains two data structures which must be merged
    """
    return {'info': ret['info'], 'dominfo': ret['dominfo']}


def VM_getInfo_Call(api, args):
    """
    The VM object has no getInfo method.  We use the method from 'Global' and
    pass arguments to get verbose information for only this one VM.
    """
    vmId = api._UUID
    return API.Global().getVMList(True, [vmId], False)


def VM_getInfo_Ret(ret):
    """
    The result will be a list with only one element.
    """
    return ret['vmList'][0]


def VM_running_state_change_Ret(ret):
    """
    The result will contain empty dict for vmList key.
    """
    return {'vmList': {}}


def VM_migrationCreate_Ret(ret):
    """
    The result contains two data structures which must be merged
    """
    return {'params': ret['params'], 'migrationPort': ret['migrationPort']}


def Volume_getsize_Ret(ret):
    """
    Merge the two sizes into a single dictionary result.
    """
    return {'truesize': ret['truesize'], 'apparentsize': ret['apparentsize']}


def Image_prepare_Ret(ret):
    return {'path': ret['path']}


##
# Possible ways to override a command:
# - Supply a custom call function if the function name doesn't map directly to
#   a vdsm API.
# - Specify the name of the field in the result that is the return value
# - Specify a custom function to post-process the result into a return value
##
command_info = {
    'Host_fenceNode': {'ret': Host_fenceNode_Ret},
    'Host_get_image_ticket': {'ret': 'result'},
    'Host_getAllTasksInfo': {'ret': 'allTasksInfo'},
    'Host_getAllTasksStatuses': {'ret': 'allTasksStatus'},
    'Host_getCapabilities': {'ret': Host_getCapabilities_Ret},
    'Host_getNetworkCapabilities': {'ret': 'info'},
    'Host_getNetworkStatistics': {'ret': 'info'},
    'Host_getConnectedStoragePools': {'ret': 'poollist'},
    'Host_getDeviceList': {'ret': 'devList'},
    'Host_getDevicesVisibility': {'ret': 'visible'},
    'Host_getExternalVMs': {'ret': 'vmList'},
    'Host_getExternalVMNames': {'ret': 'vmNames'},
    'Host_getExternalVmFromOva': {'ret': 'vmList'},
    'Host_getConvertedVm': {'ret': 'ovf'},
    'Host_getLldp': {'ret': 'info'},
    'Host_getHardwareInfo': {'ret': 'info'},
    'Host_getLVMVolumeGroups': {'ret': 'vglist'},
    'Host_getStats': {'ret': 'info'},
    'Host_getStorageDomains': {'ret': 'domlist'},
    'Host_getStorageRepoStats': {'ret': Host_getStorageRepoStats_Ret},
    'Host_hostdevListByCaps': {'ret': 'deviceList'},
    'Host_dumpxmls': {'ret': 'domxmls'},
    'Host_getVMList': {'call': Host_getVMList_Call, 'ret': 'vmList'},
    'Host_getVMFullList': {'call': Host_getVMFullList_Call, 'ret': 'vmList'},
    'Host_getAllVmStats': {'ret': 'statsList'},
    'Host_getAllVmIoTunePolicies': {'ret': 'io_tune_policies_dict'},
    'Host_setupNetworks': {'ret': 'status'},
    'Host_setKsmTune': {'ret': 'status'},
    'Host_setHaMaintenanceMode': {'ret': 'status'},
    'Host_echo': {'ret': 'logged'},
    'Image_cloneStructure': {'ret': 'uuid'},
    'Image_delete': {'ret': 'uuid'},
    'Image_deleteVolumes': {'ret': 'uuid'},
    'Image_getVolumes': {'ret': 'uuidlist'},
    'Image_download': {'ret': 'uuid'},
    'Image_move': {'ret': 'uuid'},
    'Image_reconcileVolumeChain': {'ret': 'volumes'},
    'Image_syncData': {'ret': 'uuid'},
    'Image_upload': {'ret': 'uuid'},
    'Image_prepare': {'ret': Image_prepare_Ret},
    'ISCSIConnection_discoverSendTargets': {'ret': 'fullTargets'},
    'LVMVolumeGroup_create': {'ret': 'uuid'},
    'LVMVolumeGroup_getInfo': {'ret': 'info'},
    'StorageDomain_getFileStats': {'ret': 'fileStats'},
    'StorageDomain_getImages': {'ret': 'imageslist'},
    'StorageDomain_getInfo': {'ret': 'info'},
    'StorageDomain_getStats': {'ret': 'stats'},
    'StorageDomain_getVolumes': {'ret': 'uuidlist'},
    'StorageDomain_resizePV': {'ret': 'size'},
    'StoragePool_connectStorageServer': {'ret': 'statuslist'},
    'StoragePool_disconnectStorageServer': {'ret': 'statuslist'},
    'StoragePool_fence': {'ret': 'spm_st'},
    'StoragePool_getBackedUpVmsInfo': {'ret': 'vmlist'},
    'StoragePool_getBackedUpVmsList': {'ret': 'vmlist'},
    'StoragePool_getDomainsContainingImage': {'ret': 'domainslist'},
    'StoragePool_getInfo': {'ret': StoragePool_getInfo_Ret},
    'StoragePool_getSpmStatus': {'ret': 'spm_st'},
    'StoragePool_spmStart': {'ret': 'uuid'},
    'StoragePool_upgrade': {'ret': 'upgradeStatus'},
    'StoragePool_prepareMerge': {'ret': 'uuid'},
    'StoragePool_finalizeMerge': {'ret': 'uuid'},
    'StoragePool_reduceVolume': {'ret': 'uuid'},
    'Task_getInfo': {'ret': 'TaskInfo'},
    'Task_getStatus': {'ret': 'taskStatus'},
    'VM_changeCD': {'ret': 'vmList'},
    'VM_changeFloppy': {'ret': 'vmList'},
    'VM_create': {'ret': 'vmList'},
    'VM_cont': {'ret': VM_running_state_change_Ret},
    'VM_diskSizeExtend': {'ret': 'size'},
    'VM_getDiskAlignment': {'ret': 'alignment'},
    'VM_getInfo': {'call': VM_getInfo_Call, 'ret': VM_getInfo_Ret},
    'VM_getIoTune': {'ret': 'ioTuneList'},
    'VM_getIoTunePolicy': {'ret': 'ioTunePolicyList'},
    'VM_getStats': {'ret': 'statsList'},
    'VM_hotplugDisk': {'ret': 'vmList'},
    'VM_hotplugLease': {'ret': 'vmList'},
    'VM_hotplugNic': {'ret': 'vmList'},
    'VM_hotplugHostdev': {'ret': 'assignedDevices'},
    'VM_hotunplugHostdev': {'ret': 'unpluggedDevices'},
    'VM_hotunplugDisk': {'ret': 'vmList'},
    'VM_hotunplugLease': {'ret': 'vmList'},
    'VM_hotunplugNic': {'ret': 'vmList'},
    'VM_mergeStatus': {'ret': 'mergeStatus'},
    'VM_migrationCreate': {'ret': VM_migrationCreate_Ret},
    'VM_getMigrationStatus': {'ret': 'migrationStats'},
    'VM_pause': {'ret': VM_running_state_change_Ret},
    'VM_setCpuTunePeriod': {'ret': 'status'},
    'VM_setCpuTuneQuota': {'ret': 'status'},
    'VM_hotplugMemory': {'ret': 'vmList'},
    'VM_setNumberOfCpus': {'ret': 'vmList'},
    'VM_setIoTune': {'ret': 'status'},
    'VM_setBalloonTarget': {'ret': 'status'},
    'VM_updateDevice': {'ret': 'vmList'},
    'VM_updateVmPolicy': {'ret': 'status'},
    'Volume_copy': {'ret': 'uuid'},
    'Volume_create': {'ret': 'uuid'},
    'Volume_delete': {'ret': 'uuid'},
    'Volume_getInfo': {'ret': 'info'},
    'Volume_getQemuImageInfo': {'ret': 'info'},
    'Volume_getPath': {'ret': 'path'},
    'Volume_getSize': {'ret': Volume_getsize_Ret},
    'Volume_extendSize': {'ret': 'uuid'},
    'Volume_measure': {'ret': 'result'},
    'Host_getAllTasks': {'ret': 'tasks'},
    'Host_getJobs': {'ret': 'jobs'},
    'Lease_create': {'ret': 'uuid'},
    'Lease_delete': {'ret': 'uuid'},
    'Lease_rebuild_leases': {'ret': 'uuid'},
    'Lease_info': {'ret': 'result'},
    'Lease_status': {'ret': 'result'},
    'NBD_start_server': {'ret': 'result'},
    'ManagedVolume_attach_volume': {'ret': 'result'},
    'ManagedVolume_volumes_info': {'ret': 'result'},
    'VM_start_backup': {'ret': 'result'},
    'VM_stop_backup': {'ret': 'status'},
    'VM_backup_info': {'ret': 'result'},
    'VM_delete_checkpoints': {'ret': 'status'},
    'VM_redefine_checkpoints': {'ret': 'status'},
}
