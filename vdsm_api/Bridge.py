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
import vdsmapi
import logging
import types
import API


class VdsmError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message


class DynamicBridge(object):
    def __init__(self, schema):
        self._parseSchema(schema)

    def dispatch(self, name, argobj):
        methodName = name.replace('.', '_')
        result = None
        error = {'code': 0, 'message': 'Success'}
        try:
            fn = getattr(self, methodName)
        except AttributeError:
            error = {'code': 4,
                     'message': "Operation '%s' not supported" % name}
            return {'result': result, 'error': error}
        try:
            result = fn(argobj)
        except VdsmError as e:
            error = {'code': e.code, 'message': e.message}
        return {'result': result, 'error': error}

    def _getArgs(self, argobj, arglist):
        return tuple(argobj[arg] for arg in arglist if arg in argobj)

    def _getResult(self, response, member=None):
        if member is None:
            return None
        try:
            return response[member]
        except KeyError:
            raise VdsmError(5, "Response is missing '%s' member" % member)

    def _parseSchema(self, schema):
        self.commands = {}
        self.classes = {}
        self.types = {}
        with open(schema) as f:
            symbols = vdsmapi.parse_schema(f)
            for s in symbols:
                if 'command' in s:
                    key = "%s_%s" % (s['command']['class'],
                                     s['command']['name'])
                    self.commands[key] = s
                elif 'class' in s:
                    cls = s['class']
                    self.classes[cls] = s
                elif 'type' in s:
                    t = s['type']
                    self.types[t] = s

    def __getattr__(self, attr):
        if attr in self.commands:
            className, methodName = attr.split('_')
            return partial(self._dynamicMethod, className, methodName)
        else:
            raise AttributeError()

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

    def _getMethodArgs(self, className, cmd, argObj):
        """
        An internal API call currently looks like:

            instance = API.<className>(<*ctor_args>)
            intance.<method>(<*method_args>)

        Eventually we can remove this instancing but for now that's the way it
        works.  Each API.py object defines its ctor_args so that we can query
        them from here.  For any given method, the method_args are obtained by
        chopping off the ctor_args from the beginning of argObj.
        """
        className = self._convertClassName(className)
        # Get the full argument list
        allArgs = self.commands[cmd].get('data', {}).keys()

        # Get the list of ctor_args
        ctorArgs = getattr(API, className).ctorArgs

        # Determine the method arguments by subtraction
        methodArgs = []
        for arg in allArgs:
            if arg not in ctorArgs:
                methodArgs.append(arg)

        return self._getArgs(argObj, methodArgs)

    def _getApiInstance(self, className, argObj):
        className = self._convertClassName(className)

        apiObj = getattr(API, className)
        ctorArgs = self._getArgs(argObj, apiObj.ctorArgs)
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
            symbol = self.types[symTypeName]
        except KeyError:
            return

        if isList:
            itemList = obj
        else:
            itemList = [obj]

        for item in itemList:
            if symTypeName in typefixups:
                logging.warn("Fixing up type %s", symTypeName)
                typefixups[symTypeName](item)
            for (k, v) in symbol.get('data', {}).items():
                k = self._symNameFilter(k)
                if k in item:
                    self._typeFixup(k, v, item[k])

    def _fixupArgs(self, cmd, args):
        argInfo = zip(self.commands[cmd].get('data', {}).items(), args)
        for typeInfo, val in argInfo:
            argName, argType = typeInfo
            if argType not in self.types:
                continue
            self._typeFixup(argName, argType, val)

    def _fixupRet(self, cmd, result):
        retType = self.commands[cmd].get('returns', None)
        if retType is not None:
            self._typeFixup('return', retType, result)
        return result

    def _dynamicMethod(self, className, methodName, argobj):
        cmd = '%s_%s' % (className, methodName)
        api = self._getApiInstance(className, argobj)
        methodArgs = self._getMethodArgs(className, cmd, argobj)
        self._fixupArgs(cmd, methodArgs)

        # Call the override function (if given).  Otherwise, just call directly
        fn = command_info.get(cmd, {}).get('call')
        if fn:
            result = fn(api, argobj)
        else:
            fn = getattr(api, methodName)
            result = fn(*methodArgs)

        if result['status']['code']:
            code = result['status']['code']
            msg = result['status']['message']
            raise VdsmError(code, msg)

        retfield = command_info.get(cmd, {}).get('ret')
        if isinstance(retfield, types.FunctionType):
            ret = retfield(result)
        else:
            ret = self._getResult(result, retfield)
        return self._fixupRet(cmd, ret)


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
    return API.Global().getVMList(False, vmList)


def Host_getVMList_Ret(ret):
    """
    Just return a list of VM UUIDs
    """
    return [v['vmId'] for v in ret['vmList']]


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
    return API.Global().getVMList(True, [vmId])


def VM_getInfo_Ret(ret):
    """
    The result will be a list with only one element.
    """
    return ret['vmList'][0]


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
    'Host_fenceNode': {'ret': 'power'},
    'Host_getAllTasksInfo': {'ret': 'allTasksInfo'},
    'Host_getAllTasksStatuses': {'ret': 'allTasksStatus'},
    'Host_getCapabilities': {'ret': 'info'},
    'Host_getConnectedStoragePools': {'ret': 'poollist'},
    'Host_getDeviceInfo': {'ret': 'info'},
    'Host_getDeviceList': {'ret': 'devList'},
    'Host_getDevicesVisibility': {'ret': 'visibility'},
    'Host_getLVMVolumeGroups': {'ret': 'vglist'},
    'Host_getStats': {'ret': 'info'},
    'Host_getStorageDomains': {'ret': 'domlist'},
    'Host_getStorageRepoStats': {'ret': Host_getStorageRepoStats_Ret},
    'Host_getVMList': {'call': Host_getVMList_Call, 'ret': Host_getVMList_Ret},
    'Image_delete': {'ret': 'uuid'},
    'Image_deleteVolumes': {'ret': 'uuid'},
    'Image_getVolumes': {'ret': 'uuidlist'},
    'Image_mergeSnapshots': {'ret': 'uuid'},
    'Image_move': {'ret': 'uuid'},
    'ISCSIConnection_discoverSendTargets': {'ret': 'fullTargets'},
    'LVMVolumeGroup_create': {'ret': 'uuid'},
    'LVMVolumeGroup_getInfo': {'ret': 'info'},
    'StorageDomain_getFileList': {'ret': 'files'},
    'StorageDomain_getImages': {'ret': 'imageslist'},
    'StorageDomain_getInfo': {'ret': 'info'},
    'StorageDomain_getStats': {'ret': 'stats'},
    'StorageDomain_getVolumes': {'ret': 'uuidlist'},
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
    'StoragePool_validateStorageServerConnection': {'ret': 'statuslist'},
    'Task_getInfo': {'ret': 'TaskInfo'},
    'Task_getStatus': {'ret': 'taskStatus'},
    'VM_changeCD': {'ret': 'vmList'},
    'VM_changeFloppy': {'ret': 'vmList'},
    'VM_create': {'ret': 'vmList'},
    'VM_getInfo': {'call': VM_getInfo_Call, 'ret': VM_getInfo_Ret},
    'VM_getStats': {'ret': 'statsList'},
    'VM_hotplugDisk': {'ret': 'vmList'},
    'VM_hotplugNic': {'ret': 'vmList'},
    'VM_hotUnplugDisk': {'ret': 'vmList'},
    'VM_hotUnplugNic': {'ret': 'vmList'},
    'VM_mergeStatus': {'ret': 'mergeStatus'},
    'VM_migrationCreate': {'ret': VM_migrationCreate_Ret},
    'Volume_copy': {'ret': 'uuid'},
    'Volume_create': {'ret': 'uuid'},
    'Volume_delete': {'ret': 'uuid'},
    'Volume_getInfo': {'ret': 'info'},
    'Volume_getPath': {'ret': 'path'},
    'Volume_getSize': {'ret': Volume_getsize_Ret},
    'Host_getAllTasks': {'ret': 'TasksDetails'},
}


def fieldClone(oldName, newName, obj):
    logging.warning("fieldClone: %s -> %s", oldName, newName)
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
}
