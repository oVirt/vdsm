# Copyright (C) 2012 Adam Litke, IBM Corporation
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

from functools import partial

import inspect
import threading
import types

import API
import vdsmapi
import yajsonrpc

from vdsm.netinfo import getDeviceByIP
from vdsm.exception import VdsmException


try:
    import gluster.apiwrapper as gapi
    import gluster.exception as ge
    _glusterEnabled = True
except ImportError:
    _glusterEnabled = False


class VdsmError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message

    def __str__(self):
        return '[error %d] %s' % (self.code, self.message)


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
        self.api = vdsmapi.get_api()
        self._threadLocal = threading.local()

    def register_server_address(self, server_address):
        self._threadLocal.server = server_address

    def unregister_server_address(self):
        self._threadLocal.server = None

    def dispatch(self, name, argobj):
        methodName = name.replace('.', '_')
        result = None
        try:
            fn = getattr(self, methodName)
        except AttributeError:
            raise yajsonrpc.JsonRpcMethodNotFoundError()

        try:
            result = fn(argobj)
        except VdsmError as e:
            # TBD: Do we really want to always log here
            self.log.debug("Operation failed, returning error", exc_info=True)
            raise yajsonrpc.JsonRpcError(e.code, e.message)

        return result

    def _getArgs(self, argobj, arglist, defaultArgs):
        ret = ()
        for arg in arglist:
            if arg.startswith('*'):
                name = self._symNameFilter(arg)
                if name in argobj:
                    ret = ret + (argobj[name],)
                else:
                    if len(defaultArgs) > 0:
                        ret = ret + (defaultArgs[0],)
                defaultArgs = defaultArgs[1:]
            elif arg in argobj:
                ret = ret + (argobj[arg],)
        return ret

    def _getResult(self, response, member=None):
        if member is None:
            return None
        try:
            return response[member]
        except KeyError:
            raise VdsmError(5, "Response is missing '%s' member" % member)

    def __getattr__(self, attr):
        try:
            className, methodName = attr.split('_')
            self.api['commands'][className][methodName]
        except (KeyError, ValueError):
            raise AttributeError("Attribute not found '%s'" % attr)
        return partial(self._dynamicMethod, className, methodName)

    def _convertClassName(self, name):
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

    def _getMethodArgs(self, className, methodName, argObj):
        """
        An internal API call currently looks like:

            instance = API.<className>(<*ctor_args>)
            intance.<method>(<*method_args>)

        Eventually we can remove this instancing but for now that's the way it
        works.  Each API.py object defines its ctor_args so that we can query
        them from here.  For any given method, the method_args are obtained by
        chopping off the ctor_args from the beginning of argObj.
        """
        # Get the full argument list
        sym = self.api['commands'][className][methodName]
        allArgs = sym.get('data', {}).keys()

        className = self._convertClassName(className)
        # Get the list of ctor_args
        if _glusterEnabled and className.startswith('Gluster'):
            ctorArgs = getattr(gapi, className).ctorArgs
            defaultArgs = self._getDefaultArgs(gapi, className, methodName)
        else:
            ctorArgs = getattr(API, className).ctorArgs
            defaultArgs = self._getDefaultArgs(API, className, methodName)

        # Determine the method arguments by subtraction
        methodArgs = []
        for arg in allArgs:
            name = self._symNameFilter(arg)

            if name not in ctorArgs:
                methodArgs.append(arg)

        return self._getArgs(argObj, methodArgs, defaultArgs)

    def _getDefaultArgs(self, api, className, methodName):
        result = []
        for class_name, class_obj in inspect.getmembers(api, inspect.isclass):
            if class_name == className:
                for method_name, method_obj in inspect.getmembers(
                        class_obj, inspect.ismethod):
                    if method_name == methodName:
                        args = inspect.getargspec(method_obj).defaults
                        if args:
                            result = list(args)
        return result

    def _getApiInstance(self, className, argObj):
        className = self._convertClassName(className)

        if _glusterEnabled and className.startswith('Gluster'):
            apiObj = getattr(gapi, className)
        else:
            apiObj = getattr(API, className)

        ctorArgs = self._getArgs(argObj, apiObj.ctorArgs, [])
        return apiObj(*ctorArgs)

    def _symNameFilter(self, symName):
        """
        The schema prefixes symbol names with '*' if they are optional.  Strip
        that annotation to get the correct symbol name.
        """
        if symName.startswith('*'):
            symName = symName[1:]
        return symName

    # TODO: Add support for map types
    def _typeFixup(self, symName, symTypeName, obj):
        isList = False
        if isinstance(symTypeName, list):
            symTypeName = symTypeName[0]
            isList = True
        symName = self._symNameFilter(symName)

        try:
            symbol = self.api['types'][symTypeName]
        except KeyError:
            return

        if isList:
            itemList = obj
        else:
            itemList = [obj]

        for item in itemList:
            if symTypeName in typefixups:
                typefixups[symTypeName](item)
            for (k, v) in symbol.get('data', {}).items():
                k = self._symNameFilter(k)
                if k in item:
                    self._typeFixup(k, v, item[k])

    def _fixupArgs(self, className, methodName, args):
        argDef = self.api['commands'][className][methodName].get('data', {})
        argInfo = zip(argDef.items(), args)
        for typeInfo, val in argInfo:
            argName, argType = typeInfo
            if isinstance(argType, list):
                # check type of first element
                argType = argType[0]
            if argType not in self.api['types']:
                continue
            if val is None:
                continue
            self._typeFixup(argName, argType, val)

    def _fixupRet(self, className, methodName, result):
        retType = self._getRetList(className, methodName)
        if retType is not None:
            self._typeFixup('return', retType, result)
        return result

    def _getRetList(self, className, methodName):
        return self.api['commands'][className][methodName].get('returns')

    def _nameArgs(self, args, kwargs, arglist):
        kwargs = kwargs.copy()
        for i, arg in enumerate(args):
            argName = arglist[i]
            kwargs[argName] = arg

        return kwargs

    def _getArgList(self, className, methodName):
        sym = self.api['commands'][className][methodName]
        return sym.get('data', {}).keys()

    def _dynamicMethod(self, className, methodName, *args, **kwargs):
        argobj = self._nameArgs(args, kwargs,
                                self._getArgList(className, methodName))
        api = self._getApiInstance(className, argobj)
        methodArgs = self._getMethodArgs(className, methodName, argobj)
        self._fixupArgs(className, methodName, methodArgs)

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
                raise InvalidCall(fn, methodArgs, e)
            except VdsmException as e:
                raise yajsonrpc.JsonRpcError(e.code, str(e))

        if result['status']['code']:
            code = result['status']['code']
            msg = result['status']['message']
            raise yajsonrpc.JsonRpcError(code, msg)

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
            ret = self._getResult(result, retfield)
        return self._fixupRet(className, methodName, ret)


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
    We need to add additional information to getCaps as it is done for xmlrpc.
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
    API.updateTimestamp()  # required for editNetwork flow
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


##
# Possible ways to override a command:
# - Supply a custom call function if the function name doesn't map directly to
#   a vdsm API.
# - Specify the name of the field in the result that is the return value
# - Specify a custom function to post-process the result into a return value
##
command_info = {
    'ConnectionRefs_acquire': {'ret': 'results'},
    'ConnectionRefs_release': {'ret': 'results'},
    'ConnectionRefs_statuses': {'ret': 'connectionslist'},
    'Host_fenceNode': {'ret': Host_fenceNode_Ret},
    'Host_getAllTasksInfo': {'ret': 'allTasksInfo'},
    'Host_getAllTasksStatuses': {'ret': 'allTasksStatus'},
    'Host_getCapabilities': {'ret': Host_getCapabilities_Ret},
    'Host_getConnectedStoragePools': {'ret': 'poollist'},
    'Host_getDeviceList': {'ret': 'devList'},
    'Host_getDevicesVisibility': {'ret': 'visible'},
    'Host_getExternalVMs': {'ret': 'vmList'},
    'Host_getExternalVmFromOva': {'ret': 'vmList'},
    'Host_getConvertedVm': {'ret': 'ovf'},
    'Host_getHardwareInfo': {'ret': 'info'},
    'Host_getLVMVolumeGroups': {'ret': 'vglist'},
    'Host_getStats': {'ret': 'info'},
    'Host_getStorageDomains': {'ret': 'domlist'},
    'Host_getStorageRepoStats': {'ret': Host_getStorageRepoStats_Ret},
    'Host_hostdevListByCaps': {'ret': 'deviceList'},
    'Host_hostdevChangeNumvfs': {},
    'Host_startMonitoringDomain': {},
    'Host_stopMonitoringDomain': {},
    'Host_getVMList': {'call': Host_getVMList_Call, 'ret': 'vmList'},
    'Host_getVMFullList': {'call': Host_getVMFullList_Call, 'ret': 'vmList'},
    'Host_getAllVmStats': {'ret': 'statsList'},
    'Host_setupNetworks': {'ret': 'status'},
    'Host_setKsmTune': {'ret': 'taskStatus'},
    'Image_cloneStructure': {'ret': 'uuid'},
    'Image_delete': {'ret': 'uuid'},
    'Image_deleteVolumes': {'ret': 'uuid'},
    'Image_getVolumes': {'ret': 'uuidlist'},
    'Image_download': {'ret': 'uuid'},
    'Image_mergeSnapshots': {'ret': 'uuid'},
    'Image_move': {'ret': 'uuid'},
    'Image_reconcileVolumeChain': {'ret': 'volumes'},
    'Image_syncData': {'ret': 'uuid'},
    'Image_upload': {'ret': 'uuid'},
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
    'StoragePool_getFloppyList': {'ret': 'isolist'},
    'StoragePool_getInfo': {'ret': StoragePool_getInfo_Ret},
    'StoragePool_getIsoList': {'ret': 'isolist'},
    'StoragePool_getSpmStatus': {'ret': 'spm_st'},
    'StoragePool_spmStart': {'ret': 'uuid'},
    'StoragePool_upgrade': {'ret': 'upgradeStatus'},
    'Task_getInfo': {'ret': 'TaskInfo'},
    'Task_getStatus': {'ret': 'taskStatus'},
    'VM_changeCD': {'ret': 'vmList'},
    'VM_changeFloppy': {'ret': 'vmList'},
    'VM_create': {'ret': 'vmList'},
    'VM_cont': {'ret': VM_running_state_change_Ret},
    'VM_diskSizeExtend': {'ret': 'size'},
    'VM_getDiskAlignment': {'ret': 'alignment'},
    'VM_getInfo': {'call': VM_getInfo_Call, 'ret': VM_getInfo_Ret},
    'VM_getIoTunePolicy': {'ret': 'ioTunePolicyList'},
    'VM_getStats': {'ret': 'statsList'},
    'VM_hotplugDisk': {'ret': 'vmList'},
    'VM_hotplugNic': {'ret': 'vmList'},
    'VM_hotunplugDisk': {'ret': 'vmList'},
    'VM_hotunplugNic': {'ret': 'vmList'},
    'VM_mergeStatus': {'ret': 'mergeStatus'},
    'VM_migrationCreate': {'ret': VM_migrationCreate_Ret},
    'VM_getMigrationStatus': {'ret': 'migrationStats'},
    'VM_pause': {'ret': VM_running_state_change_Ret},
    'VM_setCpuTunePeriod': {'ret': 'taskStatus'},
    'VM_setCpuTuneQuota': {'ret': 'taskStatus'},
    'VM_hotplugMemory':  {'ret': 'vmList'},
    'VM_setNumberOfCpus': {'ret': 'vmList'},
    'VM_setIoTune': {'ret': 'taskStatus'},
    'VM_updateDevice': {'ret': 'vmList'},
    'Volume_copy': {'ret': 'uuid'},
    'Volume_create': {'ret': 'uuid'},
    'Volume_delete': {'ret': 'uuid'},
    'Volume_getInfo': {'ret': 'info'},
    'Volume_getPath': {'ret': 'path'},
    'Volume_getSize': {'ret': Volume_getsize_Ret},
    'Volume_extendSize': {'ret': 'uuid'},
    'Host_getAllTasks': {'ret': 'tasks'},
}


def fieldClone(oldName, newName, obj):
    if oldName in obj:
        obj[newName] = obj[oldName]
    elif newName in obj:
        obj[oldName] = obj[newName]


typefixups = {
    'VmDevice': partial(fieldClone, 'type', 'deviceType'),
    'BlockDevicePathInfo': partial(fieldClone, 'type', 'deviceType'),
    'VolumeGroupInfo': partial(fieldClone, 'type', 'deviceType'),
    'VmDeviceAddress': partial(fieldClone, 'type', 'addressType'),
    'IscsiCredentials': partial(fieldClone, 'type', 'authType'),
    'ConnectionRefArgs': partial(fieldClone, 'type', 'connType'),
    'VolumeInfo': partial(fieldClone, 'type', 'allocType'),
    'StorageDomainInfo': partial(fieldClone, 'class', 'domainClass'),
}
