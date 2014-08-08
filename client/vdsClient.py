# Copyright 2011 Red Hat, Inc.
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

import sys
import ast
import getopt
import traceback
import xmlrpclib
import os
import re
import shlex
import socket
import string
import pprint as pp

from vdsm import utils, vdscli
try:
    import vdsClientGluster as ge
    _glusterEnabled = True
except ImportError:
    _glusterEnabled = False

BLANK_UUID = '00000000-0000-0000-0000-000000000000'

STATUS_ERROR = {'status': {'code': 100, 'message': "ERROR"}}

# Storage Domain Types
UNKNOWN_DOMAIN = 0
NFS_DOMAIN = 1
FCP_DOMAIN = 2
ISCSI_DOMAIN = 3
LOCALFS_DOMAIN = 4
CIFS_DOMAIN = 5

# Volume Types
UNKNOWN_VOL = 0
PREALLOCATED_VOL = 1
SPARSE_VOL = 2

# Volume Format
UNKNOWN_FORMAT = 3
COW_FORMAT = 4
RAW_FORMAT = 5

# Volume Role
SHARED_VOL = 6
INTERNAL_VOL = 7
LEAF_VOL = 8


def validateArgTypes(args, conv, requiredArgsNumber=0):
    if len(args) > len(conv) or len(args) < requiredArgsNumber:
        raise ValueError("Wrong number of arguments provided, "
                         "expecting %d (%d required) got %d"
                         % (len(conv), requiredArgsNumber, len(args)))

    for i in range(len(args)):
        args[i] = conv[i](args[i])


def fmt3(num):
    for x in ['', 'KB', 'MB', 'GB', 'TB']:
        if num < 1024:
            return "%3.1f%s" % (num, x)
        num /= 1024


def usage(cmd, full=True):
    print "Usage:  vdsClient [OPTIONS] <server> <command> [Command parameters]"
    print "\nOptions"
    print "-h\tDisplay this help"
    print "-m\tList supported methods and their params (Short help)"
    print "-s [--truststore path]\tConnect to server with SSL."
    print "-o, --oneliner\tShow the key-val information in one line."
    print "\tIf truststore path is not specified, use defaults."
    print
    print "Password can be provided as command line argument, path to"
    print "file with password or environment variable by providing"
    print "auth=file:path or auth=env:name or auth=pass:password"

    print "\nCommands"
    verbs = cmd.keys()
    verbs.sort()
    for entry in verbs:
        if full:
            print entry
            for line in cmd[entry][1]:
                print '\t' + line
        else:
            print entry + '\t' + cmd[entry][1][0]


def printConf(conf):
    try:
        print "\n" + conf['vmId']
        print "\tStatus = " + conf['status']
    except:
        pass
    for element in conf.keys():
        if element not in ('vmId', 'status'):
            print "\t%s = %s" % (element, conf[element])


def printDict(dict, pretty=True):
    keys = dict.keys()
    keys.sort()
    for element in keys:
        if pretty:
            representation = pp.pformat(dict[element]).replace(
                '\n', '\n\t' + ' ' * len(element + ' = '))
        else:
            representation = dict[element]
        print "\t%s = %s" % (element, representation)


def printStats(list):
    for conf in list:
        printConf(conf)


def parseArgs(args):
    lexer = shlex.shlex(args, posix=True)
    lexer.wordchars = filter(lambda x: x not in list('=,\\"\''),
                             string.printable)
    results = list(lexer)
    args = dict(zip(results[0::4], results[2::4]))
    return args


def parseConList(args):
    args = parseArgs(args)
    if 'auth' in args:
        args['password'] = getPassword(args['auth'])
        del args['auth']
    return args


def getAuthFromArgs(args, default=None):
    if 'auth' in args:
        return getPassword(args['auth'])
    return default


def getPassword(string):
    ret = None
    try:
        (method, value) = string.split(':', 1)
    except ValueError:
        raise RuntimeError('auth does not contain valid format: method:value')
    if method == 'file':
        with open(value) as f:
            ret = f.readline()
    elif method == 'env':
        ret = os.environ.get(value)
    elif method == 'pass':
        ret = value
    else:
        raise RuntimeError("unknown method %s for parameter 'auth'" % method)
    if ret is None:
        raise RuntimeError("Missing password")
    return ret


class service:
    def __init__(self):
        self.useSSL = False
        self.truststore = None
        self.pretty = True

    def do_connect(self, hostPort):
        self.s = vdscli.connect(hostPort, self.useSSL, self.truststore)

    def ExecAndExit(self, response, parameterName='none'):
        if response['status']['code'] != 0:
            print response['status']['message']
        else:
            if 'vmList' in response:
                printConf(response['vmList'])
            elif 'statsList' in response:
                if parameterName != 'none':
                    print response['statsList'][0][parameterName]
                else:
                    printStats(response['statsList'])
            elif 'info' in response:
                printDict(response['info'], self.pretty)
            else:
                printDict(response['status'], self.pretty)
        sys.exit(response['status']['code'])

    def do_create(self, args):
        params = {}
        drives = []
        devices = []
        cpuPinning = {}
        numaTune = {}
        guestNumaNodes = []
        confLines = []
        confFile = open(args[0])
        for line in confFile.readlines():
            line = re.sub("\s+", '', line)
            line = re.sub("\#.*", '', line)
            if line:
                confLines.append(line)
        if len(args) > 1:
            confLines.extend(args[1:])
        for line in confLines:
            if '=' in line:
                param, value = line.split("=", 1)
                if param == 'devices':
                    devices.append(self._parseDriveSpec(value))
                elif param == 'drive':
                    drives.append(self._parseDriveSpec(value))
                elif param == 'cpuPinning':
                    cpuPinning, rStr = self._parseNestedSpec(value)
                elif param == 'numaTune':
                    numaTune, rStr = self._parseNestedSpec(value)
                elif param == 'guestNumaNodes':
                    guestNumaNodes.append(self._parseDriveSpec(value))
                elif param.startswith('custom_'):
                    if 'custom' not in params:
                        params['custom'] = {}
                    params['custom'][param[7:]] = value
                else:
                    if param in ('cdrom', 'floppy'):
                        value = self._parseDriveSpec(value)
                    params[param] = value
            else:
                params[line.strip()] = ''
        if cpuPinning:
            params['cpuPinning'] = cpuPinning
        if numaTune:
            params['numaTune'] = numaTune
        if guestNumaNodes:
            params['guestNumaNodes'] = guestNumaNodes
        if drives:
            params['drives'] = drives
        if devices:
            params['devices'] = devices
        # Backward compatibility for vdsClient users
        if 'vt' in params:
            params['kvmEnable'] = params['vt']

        if 'imageFile' in params:
            params['hda'] = params['imageFile']

        drives = ['hdd', 'hdc', 'hdb']
        if 'moreImages' in params:
            for image in params['moreImages'].split(','):
                params[drives.pop()] = image

        if 'sysprepInf' in params:
            infFile = open(params['sysprepInf'], 'rb')
            try:
                params['sysprepInf'] = xmlrpclib.Binary(infFile.read())
            finally:
                infFile.close()

        return self.ExecAndExit(self.s.create(params))

    def vmUpdateDevice(self, args):
        params = self._eqSplit(args[1:])
        if 'portMirroring' in params:
            params['portMirroring'] = [net for net in params['portMirroring']
                                       .split(',') if net != '']
        return self.ExecAndExit(self.s.vmUpdateDevice(args[0], params))

    def hotplugNic(self, args):
        nic = self._parseDriveSpec(args[1])
        nic['type'] = 'interface'
        params = {'vmId': args[0], 'nic': nic}
        return self.ExecAndExit(self.s.hotplugNic(params))

    def hotunplugNic(self, args):
        nic = self._parseDriveSpec(args[1])
        nic['type'] = 'interface'
        params = {'vmId': args[0], 'nic': nic}
        return self.ExecAndExit(self.s.hotunplugNic(params))

    def hotplugDisk(self, args):
        drive = self._parseDriveSpec(args[1])
        drive['type'] = 'disk'
        drive['device'] = 'disk'
        params = {'vmId': args[0], 'drive': drive}
        return self.ExecAndExit(self.s.hotplugDisk(params))

    def hotunplugDisk(self, args):
        drive = self._parseDriveSpec(args[1])
        drive['type'] = 'disk'
        drive['device'] = 'disk'
        params = {'vmId': args[0], 'drive': drive}
        return self.ExecAndExit(self.s.hotunplugDisk(params))

    def setNumberOfCpus(self, args):
        return self.ExecAndExit(self.s.setNumberOfCpus(args[0], args[1]))

    def updateVmPolicy(self, args):
        params = {'vmId': args[0], 'vcpuLimit': args[1]}
        return self.ExecAndExit(self.s.updateVmPolicy(params))

    def do_changeCD(self, args):
        vmId = args[0]
        file = self._parseDriveSpec(args[1])
        return self.ExecAndExit(self.s.changeCD(vmId, file))

    def do_changeFloppy(self, args):
        vmId = args[0]
        file = self._parseDriveSpec(args[1])
        return self.ExecAndExit(self.s.changeFloppy(vmId, file))

    def do_list(self, args):
        """
        Usage: vdsClient 0 list [table/long/ids] [vms:vmId1,vmId2]
        """
        def _vmsParser(vmsParam):
            vmsList = vmsParam.split(':')[1].strip()
            if vmsList:
                vmsList = [vm.strip() for vm in vmsList.split(',')]
            else:
                raise ValueError('Empty VMs list.')
            return vmsList

        vmListViews = ('table', 'long', 'ids')
        view = 'long'  # Default view
        vms = []

        if args:
            if args[0].startswith('vms:'):
                vms = _vmsParser(args[0])
            else:
                view = args[0]
                if len(args) > 1 and args[1].startswith('vms:'):
                    vms = _vmsParser(args[1])

            if view not in vmListViews:
                raise ValueError('Invalid argument "%s".' % view)
            if view == 'table':
                allStats = {}

                response = self.s.getAllVmStats()
                if response['status']['code']:
                    return (response['status']['code'],
                            response['status']['message'])

                for res in response['statsList']:
                    if not vms or res['vmId'] in vms:
                        allStats[res['vmId']] = res

        response = self.s.list(True, vms)
        if response['status']['code']:
            return response['status']['code'], response['status']['message']

        for conf in response['vmList']:
            if view == 'long':
                if 'sysprepInf' in conf:
                    conf['sysprepInf'] = '<<exists>>'
                printConf(conf)

            elif view == 'table':
                vmId = conf['vmId']
                if vmId not in allStats:  # Avoid race.
                    continue
                status = conf['status']
                if allStats[vmId].get('monitorResponse') == '-1':
                    status += '*'
                print("%-36s %6s  %-20s %-20s %-20s" %
                      (vmId, conf.get('pid', 'none'),
                       conf.get('vmName', '<< NO NAME >>'),
                       status, allStats[vmId].get('guestIPs', '')))

            elif view == 'ids':
                print conf['vmId']

        sys.exit(response['status']['code'])

    def do_destroy(self, args):
        vmId = args[0]
        response = self.s.destroy(vmId)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def do_pause(self, args):
        vmId = args[0]
        return self.ExecAndExit(self.s.pause(vmId))

    def do_continue(self, args):
        vmId = args[0]
        response = self.s.cont(vmId)
        return self.ExecAndExit(response)

    def do_shutdown(self, args):
        vmId, delay, message = args[:3]
        if len(args) > 3:
            reboot = utils.tobool(args[3])
            if len(args) > 4:
                timeout = args[4]
                force = len(args) > 5 and utils.tobool(args[5])
                response = self.s.shutdown(vmId, delay, message, reboot,
                                           timeout, force)
            else:
                response = self.s.shutdown(vmId, delay, message, reboot)
        else:
            response = self.s.shutdown(vmId, delay, message)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def do_setVmTicket(self, args):
        extra_args = []
        if '--' in args:
            extra_args = args[args.index('--') + 1:]
            args = args[:args.index('--')]

        if len(args) == 3:
            vmId, otp64, secs = args[:3]
            connAct = 'disconnect'
            params = {}
        else:
            vmId, otp64, secs, connAct = args[:4]
            params = {}

        if (len(args) > 4):
            params = self._parseDriveSpec(args[4])

        if extra_args:
            parsed_args = parseArgs(extra_args[0])
            otp64 = getAuthFromArgs(parsed_args, otp64)

        return self.ExecAndExit(self.s.setVmTicket(vmId, otp64, secs, connAct,
                                params))

    def do_reset(self, args):
        vmId = args[0]
        return self.ExecAndExit(self.s.reset(vmId))

    def monitorCommand(self, args):
        vmId = args[0]
        cmd = args[1]
        response = self.s.monitorCommand(vmId, cmd)
        if response['status']['code']:
            print response['status']['message']
        else:
            for line in response['output']:
                print line
        sys.exit(response['status']['code'])

    def do_newDisk(self, args):
        file, size = args
        response = self.s.newDisk(file, size)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def do_sendkeys(self, args):
        vmId = args[0]
        return self.ExecAndExit(self.s.sendkeys(vmId, args[1:]))

    def hibernate(self, args):
        vmId, hiberVolHandle = args[0], args[1]
        response = self.s.hibernate(vmId, hiberVolHandle)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def do_migrate(self, args):
        params = {}
        if len(args) > 0:
            for line in args:
                param, value = line.split("=")
                params[param] = value
        else:
            raise Exception("Not enough parameters")
        response = self.s.migrate(params)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def do_mStat(self, args):
        vmId = args[0]
        response = self.s.migrateStatus(vmId)
        if not response['status']['code']:
            print(response['status']['message'] +
                  ' ' + str(response['progress']) + '%')
        else:
            print response['status']['message']
        sys.exit(response['status']['code'])

    def do_mCancel(self, args):
        vmId = args[0]
        response = self.s.migrateCancel(vmId)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def do_getCap(self, args):
        return self.ExecAndExit(self.s.getVdsCapabilities())

    def do_getHardware(self, args):
        return self.ExecAndExit(self.s.getVdsHardwareInfo())

    def do_getVdsStats(self, args):
        return self.ExecAndExit(self.s.getVdsStats())

    def do_getVmStats(self, args):
        vmId = args[0]
        if len(args) > 1:
            return self.ExecAndExit(self.s.getVmStats(vmId), args[1])
        else:
            return self.ExecAndExit(self.s.getVmStats(vmId))

    def do_getAllVmStats(self, args):
        return self.ExecAndExit(self.s.getAllVmStats())

    def desktopLogin(self, args):
        vmId, domain, user, password = tuple(args[:4])
        if len(args) > 4:
            extra_args = parseArgs(args[4])
            password = getAuthFromArgs(extra_args, password)
        response = self.s.desktopLogin(vmId, domain, user, password)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def desktopLock(self, args):
        vmId = args[0]
        response = self.s.desktopLock(vmId)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def desktopLogoff(self, args):
        vmId, force = tuple(args)
        response = self.s.desktopLogoff(vmId, force)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def sendHcCmd(self, args):
        vmId, message = tuple(args)
        response = self.s.sendHcCmdToDesktop(vmId, message)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def getDiskAlignment(self, args):
        driveSpecs = {}
        driveSpecs['device'] = 'disk'
        vmId = BLANK_UUID if args[0] == '0' else args[0]
        if len(args) > 2:
            driveSpecs['poolID'] = args[1]
            driveSpecs['domainID'] = args[2]
            driveSpecs['imageID'] = args[3]
            driveSpecs['volumeID'] = args[4]
        else:
            driveSpecs['GUID'] = args[1]
        res = self.s.getDiskAlignment(vmId, driveSpecs)
        if res['status'] == 0:
            for pName, aligned in res['alignment'].items():
                print "\t%s = %s" % (pName, aligned)
        else:
            print "Error in scan disk alignment"
        sys.exit(0)

    def merge(self, args):
        params = [args[0]]
        params.append(self._parseDriveSpec(args[1]))
        params.extend(args[2:])
        response = self.s.merge(*params)
        print response['status']['message']
        sys.exit(response['status']['code'])

# ####### IRS methods #######
    def createStorageDomain(self, args):
        validateArgTypes(args, [int, str, str, str, int, int])
        dom = self.s.createStorageDomain(*args)
        if dom['status']['code']:
            return dom['status']['code'], dom['status']['message']
        return 0, ''

    def setStorageDomainDescription(self, args):
        sdUUID = args[0]
        descr = args[1]
        dom = self.s.setStorageDomainDescription(sdUUID, descr)
        if dom['status']['code']:
            return dom['status']['code'], dom['status']['message']
        return 0, ''

    def validateStorageDomain(self, args):
        sdUUID = args[0]
        dom = self.s.validateStorageDomain(sdUUID)
        if dom['status']['code']:
            return dom['status']['code'], dom['status']['message']
        return 0, ''

    def activateStorageDomain(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        dom = self.s.activateStorageDomain(sdUUID, spUUID)
        if dom['status']['code']:
            return dom['status']['code'], dom['status']['message']
        return 0, ''

    def deactivateStorageDomain(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        msdUUID = args[2]
        mVer = int(args[3])
        dom = self.s.deactivateStorageDomain(sdUUID, spUUID, msdUUID, mVer)
        if dom['status']['code']:
            return dom['status']['code'], dom['status']['message']
        return 0, ''

    def attachStorageDomain(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        dom = self.s.attachStorageDomain(sdUUID, spUUID)
        if dom['status']['code']:
            return dom['status']['code'], dom['status']['message']
        return 0, ''

    def detachStorageDomain(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        msdUUID = args[2]
        mVer = int(args[3])
        dom = self.s.detachStorageDomain(sdUUID, spUUID, msdUUID, mVer)
        if dom['status']['code']:
            return dom['status']['code'], dom['status']['message']
        return 0, ''

    def forcedDetachStorageDomain(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        dom = self.s.forcedDetachStorageDomain(sdUUID, spUUID)
        if dom['status']['code']:
            return dom['status']['code'], dom['status']['message']
        return 0, ''

    def formatStorageDomain(self, args):
        sdUUID = args[0]
        if len(args) > 1:
            autoDetach = args[1]
        else:
            autoDetach = 'False'
        dom = self.s.formatStorageDomain(sdUUID, autoDetach)
        if dom['status']['code']:
            return dom['status']['code'], dom['status']['message']
        return 0, ''

    def getStorageDomainInfo(self, args):
        sdUUID = args[0]
        info = self.s.getStorageDomainInfo(sdUUID)
        if info['status']['code']:
            return info['status']['code'], info['status']['message']
        for element in info['info'].keys():
            print "\t%s = %s" % (element, info['info'][element])
        return 0, ''

    def getStorageDomainStats(self, args):
        sdUUID = args[0]
        stats = self.s.getStorageDomainStats(sdUUID)
        if stats['status']['code']:
            return stats['status']['code'], stats['status']['message']
        dt = stats['stats']['disktotal']
        df = stats['stats']['diskfree']
        print "\tdisktotal = %s (%s)" % (dt, fmt3(int(dt)))
        print "\tdiskfree  = %s (%s)" % (df, fmt3(int(df)))
        return 0, ''

    def getStorageDomainsList(self, args):
        if len(args) > 0:
            spUUID = args[0]
        else:
            spUUID = BLANK_UUID
        domains = self.s.getStorageDomainsList(spUUID)
        if domains['status']['code']:
            return domains['status']['code'], domains['status']['message']
        for entry in domains['domlist']:
            print entry
        return 0, ''

    def getDeviceList(self, args):
        devices = self.s.getDeviceList(*args)
        if devices['status']['code']:
            return devices['status']['code'], devices['status']['message']
        pp.pprint(devices['devList'])
        return 0, ''

    def getDevicesVisibility(self, args):
        devList = args[0].split(',')
        res = self.s.getDevicesVisibility(devList, {})
        if res['status']['code']:
            return res['status']['code'], res['status']['message']
        for guid, visible in res['visible'].iteritems():
            print '\t%s = %s' % (guid, visible)
        return 0, ''

    def getVGList(self, args):
        if len(args) > 0:
            storageType = int(args[0])
            vgs = self.s.getVGList(storageType)
        else:
            vgs = self.s.getVGList()

        if vgs['status']['code']:
            return vgs['status']['code'], vgs['status']['message']
        for entry in vgs['vglist']:
            print '============================'
            for element in entry.keys():
                print "%s = %s " % (element, entry[element])
        return 0, ''

    def getVGInfo(self, args):
        vgUUID = args[0]
        info = self.s.getVGInfo(vgUUID)
        if info['status']['code']:
            return info['status']['code'], info['status']['message']
        # print info['info']
        for entry in info['info'].keys():
            print '============================'
            if entry != 'pvlist':
                print "%s = %s " % (entry, info['info'][entry])
            else:
                print 'pvlist:'
                for item in info['info'][entry]:
                    for i in item.keys():
                        print "%s = %s " % (i, item[i]),
                    print
        return 0, ''

    def createVG(self, args):
        sdUUID = args[0]
        devList = args[1].split(',')
        force = args[2].capitalize() == "True" if len(args) > 2 else False
        dom = self.s.createVG(sdUUID, devList, force)
        if dom['status']['code']:
            return dom['status']['code'], dom['status']['message']
        return 0, dom['uuid']

    def removeVG(self, args):
        vgUUID = args[0]
        dom = self.s.removeVG(vgUUID)
        if dom['status']['code']:
            return dom['status']['code'], dom['status']['message']
        return 0, ''

    def extendStorageDomain(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        devList = args[2].split(',')
        dom = self.s.extendStorageDomain(sdUUID, spUUID, devList)
        if dom['status']['code']:
            return dom['status']['code'], dom['status']['message']
        return 0, ''

    def discoverST(self, args):
        portal = args[0].split(":")
        ip = portal[0]
        port = "3260"
        if len(portal) > 1:
            port = portal[1]
        if len(args) == 1:
            username = password = ""
        else:
            username = args[1]
            password = args[2]
            if len(args) > 3:
                extra_args = parseArgs(args[3])
                password = getAuthFromArgs(extra_args, password)

        con = dict(id="", connection=ip, port=port, iqn="", portal="",
                   user=username, password=password)

        targets = self.s.discoverSendTargets(con)
        if targets['status']['code']:
            return targets['status']['code'], targets['status']['message']

        print "---- fullTargets"
        for target in targets['fullTargets']:
            print target
        print "---- targets"
        for target in targets['targets']:
            print target
        return 0, ''

    def cleanupUnusedConnections(self, args):
        res = self.s.cleanupUnusedConnections()
        return res['status']['code'], res['status']['message']

    def connectStorageServer(self, args):
        serverType = int(args[0])
        spUUID = args[1]
        conList = [parseConList(args[2])]
        res = self.s.connectStorageServer(serverType, spUUID, conList)
        if res['status']['code']:
            return res['status']['code'], res['status']['message']
        return 0, ''

    def validateStorageServerConnection(self, args):
        serverType = int(args[0])
        spUUID = args[1]
        conList = [parseConList(args[2])]
        res = self.s.validateStorageServerConnection(serverType,
                                                     spUUID, conList)
        if res['status']['code']:
            return res['status']['code'], res['status']['message']
        else:
            for i in res['statuslist']:
                print "Connection id %s - status %s" % (i['id'], i['status'])
        return 0, ''

    def disconnectStorageServer(self, args):
        serverType = int(args[0])
        spUUID = args[1]
        conList = [parseConList(args[2])]
        res = self.s.disconnectStorageServer(serverType, spUUID, conList)
        if res['status']['code']:
            return res['status']['code'], res['status']['message']
        return 0, ''

    def spmStart(self, args):
        validateArgTypes(args, [str, int, int, int, str, int, int],
                         requiredArgsNumber=5)
        status = self.s.spmStart(*args)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        return 0, status['uuid']

    def spmStop(self, args):
        spUUID = args[0]
        status = self.s.spmStop(spUUID)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        return 0, ''

    def getSpmStatus(self, args):
        spUUID = args[0]
        status = self.s.getSpmStatus(spUUID)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        for element in status['spm_st'].keys():
            print "\t%s = %s" % (element, status['spm_st'][element])
        return 0, ''

    def fenceSpmStorage(self, args):
        spUUID = args[0]
        prevID = int(args[1])
        prevLVER = int(args[2])
        status = self.s.fenceSpmStorage(spUUID, prevID, prevLVER)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        for element in status['spm_st'].keys():
            print "\t%s = %s" % (element, status['spm_st'][element])
        return 0, ''

    def updateVM(self, args):
        spUUID = args[0]
        params = args[1].split(',')
        if len(args) >= 3:
            sdUUID = args[2]
        else:
            sdUUID = BLANK_UUID
        vmList = []
        vm = {}
        for item in params:
            key, value = item.split('=')
            if key == 'imglist':
                value = value.replace('+', ',')
            vm[key] = value
        vmList.append(vm)
        res = self.s.updateVM(spUUID, vmList, sdUUID)
        if res['status']['code']:
            return res['status']['code'], res['status']['message']
        return 0, ''

    def upgradeStoragePool(self, args):
        validateArgTypes(args, [str, int], True)
        status = self.s.upgradeStoragePool(*args)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        return 0, status['upgradeStatus']

    def removeVM(self, args):
        spUUID = args[0]
        vmUUID = args[1]
        if len(args) >= 3:
            sdUUID = args[2]
        else:
            sdUUID = BLANK_UUID
        res = self.s.removeVM(spUUID, vmUUID, sdUUID)
        if res['status']['code']:
            return res['status']['code'], res['status']['message']
        return 0, ''

    def _parseDomainsMap(self, domMapString):
        """
        Parse domains map string: "sdUUID1=status1,sdUUID2=status2,..."
        into a dictionary: {'sdUUID1': 'status1', 'sdUUID2': 'status2', ...}
        """
        return dict(x.split("=", 1) for x in domMapString.split(','))

    def reconstructMaster(self, args):
        spUUID = args[0]
        poolName = args[1]
        masterDom = args[2]
        domDict = self._parseDomainsMap(args[3])
        mVer = int(args[4])
        if len(args) > 5:
            st = self.s.reconstructMaster(spUUID, poolName, masterDom, domDict,
                                          mVer, *map(int, args[5:]))
        else:
            st = self.s.reconstructMaster(spUUID, poolName, masterDom, domDict,
                                          mVer)
        if st['status']['code']:
            return st['status']['code'], st['status']['message']
        return 0, ''

    def createStoragePool(self, args):
        poolType = int(args[0])
        spUUID = args[1]
        poolName = args[2]
        masterDom = args[3]
        domList = args[4].split(",")
        mVer = int(args[5])
        pool = None
        if len(args) > 6:
            pool = self.s.createStoragePool(poolType, spUUID,
                                            poolName, masterDom,
                                            domList, mVer, *args[6:])
        else:
            pool = self.s.createStoragePool(poolType, spUUID,
                                            poolName, masterDom,
                                            domList, mVer)
        if pool['status']['code']:
            return pool['status']['code'], pool['status']['message']
        return 0, ''

    def destroyStoragePool(self, args):
        spUUID = args[0]
        ID = int(args[1])
        scsi_key = args[2]
        pool = self.s.destroyStoragePool(spUUID, ID, scsi_key)
        if pool['status']['code']:
            return pool['status']['code'], pool['status']['message']
        return 0, ''

    def connectStoragePool(self, args):
        spUUID = args[0]
        ID = int(args[1])
        scsi_key = args[2]
        if len(args) > 3:
            master = args[3]
        else:
            master = BLANK_UUID
        if len(args) > 4:
            master_ver = int(args[4])
        else:
            master_ver = -1
        connectArguments = [spUUID, ID, scsi_key, master, master_ver]
        if len(args) > 5:
            connectArguments.append(self._parseDomainsMap(args[5]))
        pool = self.s.connectStoragePool(*connectArguments)
        if pool['status']['code']:
            return pool['status']['code'], pool['status']['message']
        return 0, ''

    def disconnectStoragePool(self, args):
        spUUID = args[0]
        ID = int(args[1])
        scsi_key = args[2]
        pool = self.s.disconnectStoragePool(spUUID, ID, scsi_key)
        if pool['status']['code']:
            return pool['status']['code'], pool['status']['message']
        return 0, ''

    def refreshStoragePool(self, args):
        spUUID = args[0]
        msdUUID = args[1]
        masterVersion = int(args[2])
        pool = self.s.refreshStoragePool(spUUID, msdUUID, masterVersion)
        if pool['status']['code']:
            return pool['status']['code'], pool['status']['message']
        return 0, ''

    def setStoragePoolDescription(self, args):
        spUUID = args[0]
        descr = args[1]
        dom = self.s.setStoragePoolDescription(spUUID, descr)
        if dom['status']['code']:
            return dom['status']['code'], dom['status']['message']
        return 0, ''

    def getStoragePoolInfo(self, args):
        spUUID = args[0]
        info = self.s.getStoragePoolInfo(spUUID)
        if info['status']['code']:
            return info['status']['code'], info['status']['message']
        for element in info['info'].keys():
            print "\t%s = %s" % (element, info['info'][element])
        for element in info['dominfo'].keys():
            print "\t%s = %s" % (element, info['dominfo'][element])
        return 0, ''

    def createVolume(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        diskSize = int(args[3])
        convertFactor = 2097152
        size = diskSize * convertFactor
        volFormat = int(args[4])
        preallocate = int(args[5])
        diskType = int(args[6])
        newVol = args[7]
        descr = args[8]
        if len(args) > 9:
            srcImgUUID = args[9]
        else:
            srcImgUUID = BLANK_UUID
        if len(args) > 10:
            srcVolUUID = args[10]
        else:
            srcVolUUID = BLANK_UUID
        image = self.s.createVolume(sdUUID, spUUID, imgUUID, size,
                                    volFormat, preallocate,
                                    diskType, newVol, descr,
                                    srcImgUUID, srcVolUUID)
        if image['status']['code']:
            return image['status']['code'], image['status']['message']
        return 0, image['uuid']

    def getVolumeInfo(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        volUUID = args[3]
        info = self.s.getVolumeInfo(sdUUID, spUUID, imgUUID, volUUID)
        if info['status']['code']:
            return info['status']['code'], info['status']['message']
        for element in info['info'].keys():
            print "\t%s = %s" % (element, info['info'][element])
        return 0, ''

    def getVolumePath(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        uuid = args[3]
        info = self.s.getVolumePath(sdUUID, spUUID, imgUUID, uuid)
        if info['status']['code']:
            return info['status']['code'], info['status']['message']
        return 0, info['path']

    def getVolumeSize(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        uuid = args[3]
        size = self.s.getVolumeSize(sdUUID, spUUID, imgUUID, uuid)
        if size['status']['code']:
            return size['status']['code'], size['status']['message']
        del size['status']
        printDict(size, self.pretty)
        return 0, ''

    def extendVolumeSize(self, args):
        spUUID, sdUUID, imgUUID, volUUID, newSize = args
        status = self.s.extendVolumeSize(
            spUUID, sdUUID, imgUUID, volUUID, newSize)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        return 0, ''

    def setVolumeDescription(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        volUUID = args[3]
        descr = args[4]
        status = self.s.setVolumeDescription(sdUUID, spUUID, imgUUID,
                                             volUUID, descr)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        return 0, ''

    def setVolumeLegality(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        volUUID = args[3]
        legality = args[4]
        image = self.s.setVolumeLegality(sdUUID, spUUID, imgUUID,
                                         volUUID, legality)
        return image['status']['code'], image['status']['message']

    def deleteVolume(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        volUUID = args[3].split(',')
        if len(args) > 4:
            postZero = args[4]
        else:
            postZero = 'False'
        if len(args) > 5:
            force = args[5]
        else:
            force = 'False'
        status = self.s.deleteVolume(sdUUID, spUUID, imgUUID,
                                     volUUID, postZero, force)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        return 0, status['uuid']

    def deleteVolumeByDescr(self, args):
        sdUUID = args[1]
        spUUID = args[2]
        imgUUID = args[3]
        volumes = self.s.getVolumesList(sdUUID, spUUID, imgUUID)
        todelete = []
        if volumes['status']['code']:
            return volumes['status']['code'], volumes['status']['message']
        print "Images to delete:"
        for entry in volumes['uuidlist']:
            info = self.s.getVolumeInfo(sdUUID, spUUID, imgUUID, entry)['info']
            if info['description']:
                if args[0] in info['description']:
                    print "\t" + entry + " : " + info['description']
                    todelete.append(entry)
        if not len(todelete):
            return 0, 'Nothing to delete'
        var = raw_input("Are you sure yes/no?[no] :")
        if var == "yes":
            print self.s.deleteVolume(sdUUID, spUUID, imgUUID,
                                      todelete, 'false')
        return 0, ''

    def getVolumesList(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        if len(args) > 2:
            images = [args[2]]
        else:
            imgs = self.s.getImagesList(sdUUID)
            if imgs['status']['code'] == 0:
                images = imgs['imageslist']

        for imgUUID in images:
            volumes = self.s.getVolumesList(sdUUID, spUUID, imgUUID)
            if volumes['status']['code']:
                return volumes['status']['code'], volumes['status']['message']

            for entry in volumes['uuidlist']:
                message = entry + ' : '
                res = self.s.getVolumeInfo(sdUUID, spUUID, imgUUID, entry)
                if 'info' not in res:
                    print 'ERROR:', entry, ':', res
                    continue
                info = res['info']
                if info['description']:
                    message += info['description'] + '. '
                if BLANK_UUID not in info['parent']:
                    message += 'Parent is ' + info['parent']
                print message
        return 0, ''

    def getFileStats(self, args):
        assert args
        validateArgTypes(args, [str, str])
        response = self.s.getFileStats(*args)
        if response['status']['code']:
            return response['status']['code'], response['status']['message']

        for key, value in response['fileStats'].iteritems():
            print 'file: ', key, 'stats: ', value

        return 0, ''

    def getIsoList(self, args):
        spUUID = args[0]
        isos = self.s.getIsoList(spUUID)
        if isos['status']['code']:
            return isos['status']['code'], isos['status']['message']

        print '------ ISO list with proper permissions only -------'
        for entry in isos['isolist']:
            print entry
        return 0, ''

    def getFloppyList(self, args):
        spUUID = args[0]
        floppies = self.s.getFloppyList(spUUID)
        if floppies['status']['code']:
            return floppies['status']['code'], floppies['status']['message']
        for entry in floppies['isolist']:
            print entry
        return 0, ''

    def getImagesList(self, args):
        sdUUID = args[0]
        images = self.s.getImagesList(sdUUID)
        if images['status']['code']:
            return images['status']['code'], images['status']['message']
        for entry in images['imageslist']:
            print entry
        return 0, ''

    def getImageDomainsList(self, args):
        spUUID = args[0]
        imgUUID = args[1]
        domains = self.s.getImageDomainsList(spUUID, imgUUID)
        if domains['status']['code']:
            return domains['status']['code'], domains['status']['message']
        for entry in domains['domainslist']:
            print entry
        return 0, ''

    def getConnectedStoragePoolsList(self, args):
        pools = self.s.getConnectedStoragePoolsList()
        if pools['status']['code']:
            return pools['status']['code'], pools['status']['message']
        for entry in pools['poollist']:
            print entry
        return 0, ''

    def getTaskInfo(self, args):
        taskID = args[0]
        status = self.s.getTaskInfo(taskID)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        for k, v in status['TaskInfo'].iteritems():
            print '\t', k, '=', v
        return 0, ''

    def getAllTasksInfo(self, args):
        status = self.s.getAllTasksInfo()
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        for t, inf in status['allTasksInfo'].iteritems():
            print t, ':'
            for k, v in inf.iteritems():
                print '\t', k, '=', v
        return 0, ''

    def getTaskStatus(self, args):
        taskID = args[0]
        status = self.s.getTaskStatus(taskID)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        print "TASK: %s STATUS: %s RESULT: %s MESSAGE: '%s'" % (
            taskID,
            status["taskStatus"]["taskState"],
            status["taskStatus"]["taskResult"],
            status["taskStatus"]["message"])
        print "%s" % status  # TODO

        return 0, ''

    def getAllTasksStatuses(self, args):
        status = self.s.getAllTasksStatuses()
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        print status  # TODO
        return 0, ''

    def getAllTasks(self, args):
        keys = []
        if len(args) > 0:
            keys = [x.strip() for x in args[0].split(',')]
        status = self.s.getAllTasks(keys)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        for t, inf in status['tasks'].iteritems():
            print t, ':'
            for k, v in inf.iteritems():
                print '\t', k, '=', v
        return 0, ''

    def stopTask(self, args):
        taskID = args[0]
        status = self.s.stopTask(taskID)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        print status  # TODO
        return 0, ''

    def clearTask(self, args):
        taskID = args[0]
        status = self.s.clearTask(taskID)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        print status  # TODO
        return 0, ''

    def revertTask(self, args):
        taskID = args[0]
        status = self.s.revertTask(taskID)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        print status  # TODO
        return 0, ''

    def getParent(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        uuid = args[3]
        image = self.s.getVolumeInfo(sdUUID, spUUID, imgUUID, uuid)
        if image['status']['code']:
            return image['status']['code'], image['status']['message']
        if '00000000-0000-0000-0000-000000000000' in image['info']['parent']:
            return 1, 'No parent available'
        return 0, image['info']['parent']

    def deleteImage(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        if len(args) > 3:
            postZero = args[3]
        else:
            postZero = 'False'
        if len(args) > 4:
            force = args[4]
        else:
            force = 'False'
        image = self.s.deleteImage(sdUUID, spUUID, imgUUID, postZero, force)
        if image['status']['code']:
            return image['status']['code'], image['status']['message']
        return 0, image['uuid']

    def moveImage(self, args):
        spUUID = args[0]
        srcDomUUID = args[1]
        dstDomUUID = args[2]
        imgUUID = args[3]
        vmUUID = args[4]
        op = int(args[5])
        if len(args) > 6:
            postZero = args[6]
        else:
            postZero = 'False'
        if len(args) > 7:
            force = args[7]
        else:
            force = 'False'
        image = self.s.moveImage(spUUID, srcDomUUID, dstDomUUID,
                                 imgUUID, vmUUID, op, postZero, force)
        if image['status']['code']:
            return image['status']['code'], image['status']['message']
        return 0, image['uuid']

    def sparsifyImage(self, args):
        (spUUID, tmpSdUUID, tmpImgUUID, tmpVolUUID, dstSdUUID, dstImgUUID,
         dstVolUUID) = args
        status = self.s.sparsifyImage(spUUID, tmpSdUUID, tmpImgUUID,
                                      tmpVolUUID, dstSdUUID, dstImgUUID,
                                      dstVolUUID)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        return 0, status['uuid']

    def cloneImageStructure(self, args):
        spUUID, sdUUID, imgUUID, dstSdUUID = args
        image = self.s.cloneImageStructure(spUUID, sdUUID, imgUUID, dstSdUUID)

        if image['status']['code']:
            return image['status']['code'], image['status']['message']

        return 0, image['uuid']

    def syncImageData(self, args):
        spUUID, sdUUID, imgUUID, dstSdUUID, syncType = args

        image = self.s.syncImageData(spUUID, sdUUID, imgUUID, dstSdUUID,
                                     syncType)

        if image['status']['code']:
            return image['status']['code'], image['status']['message']

        return 0, image['uuid']

    def downloadImage(self, args):
        methodArgs, spUUID, sdUUID, imgUUID, volUUID = args
        methodArgsValue = ast.literal_eval(methodArgs)

        image = self.s.downloadImage(
            methodArgsValue, spUUID, sdUUID, imgUUID, volUUID)

        if image['status']['code']:
            return image['status']['code'], image['status']['message']

        return 0, image['uuid']

    def uploadImage(self, args):
        methodArgs, spUUID, sdUUID, imgUUID, volUUID = args
        methodArgsValue = ast.literal_eval(methodArgs)

        image = self.s.uploadImage(
            methodArgsValue, spUUID, sdUUID, imgUUID, volUUID)

        if image['status']['code']:
            return image['status']['code'], image['status']['message']

        return 0, image['uuid']

    def prepareImage(self, args):
        if len(args) < 3 or len(args) > 4:
            raise ValueError('Wrong number of parameters')

        ret = self.s.prepareImage(*args)

        if 'info' in ret:
            pp.pprint(ret['info'])

        if ret['status']['code']:
            return ret['status']['code'], ret['status']['message']

        return 0, ''

    def teardownImage(self, args):
        if len(args) < 3 or len(args) > 4:
            raise ValueError('Wrong number of parameters')

        ret = self.s.teardownImage(*args)

        return ret['status']['code'], ret['status']['message']

    def moveMultiImage(self, args):
        spUUID = args[0]
        srcDomUUID = args[1]
        dstDomUUID = args[2]
        imgList = args[3].split(",")
        imgDict = {}
        for item in imgList:
            key, value = item.split('=')
            imgDict[key] = value
        vmUUID = args[4]
        if len(args) > 5:
            force = args[5]
        else:
            force = 'False'
        image = self.s.moveMultipleImages(spUUID, srcDomUUID, dstDomUUID,
                                          imgDict, vmUUID, force)
        if image['status']['code']:
            return image['status']['code'], image['status']['message']
        return 0, image['uuid']

    def copyImage(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        vmUUID = args[2]
        srcImgUUID = args[3]
        srcVolUUID = args[4]
        dstImgUUID = args[5]
        dstVolUUID = args[6]
        descr = args[7]
        if len(args) > 8:
            dstSdUUID = args[8]
        else:
            dstSdUUID = BLANK_UUID
        if len(args) > 9:
            volType = int(args[9])
        else:
            volType = SHARED_VOL
        if len(args) > 10:
            volFormat = int(args[10])
        else:
            volFormat = UNKNOWN_VOL
        if len(args) > 11:
            preallocate = int(args[11])
        else:
            preallocate = UNKNOWN_VOL
        if len(args) > 12:
            postZero = args[12]
        else:
            postZero = 'False'
        if len(args) > 13:
            force = args[13]
        else:
            force = 'False'
        image = self.s.copyImage(sdUUID, spUUID, vmUUID, srcImgUUID,
                                 srcVolUUID, dstImgUUID, dstVolUUID,
                                 descr, dstSdUUID, volType, volFormat,
                                 preallocate, postZero, force)
        if image['status']['code']:
            return image['status']['code'], image['status']['message']
        return 0, image['uuid']

    def mergeSnapshots(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        vmUUID = args[2]
        imgUUID = args[3]
        ancestor = args[4]
        successor = args[5]
        if len(args) > 6:
            postZero = args[6]
        else:
            postZero = 'False'
        image = self.s.mergeSnapshots(sdUUID, spUUID, vmUUID, imgUUID,
                                      ancestor, successor, postZero)
        if image['status']['code']:
            return image['status']['code'], image['status']['message']
        return 0, image['uuid']

    def acquireDomainLock(self, args):
        spUUID = args[0]
        sdUUID = args[1]
        image = self.s.acquireDomainLock(spUUID, sdUUID)
        if image['status']['code']:
            return image['status']['code'], image['status']['message']
        return 0, ''

    def releaseDomainLock(self, args):
        spUUID = args[0]
        sdUUID = args[1]
        image = self.s.releaseDomainLock(spUUID, sdUUID)
        if image['status']['code']:
            return image['status']['code'], image['status']['message']
        return 0, ''

    def prepareForShutdown(self, args):
        stats = self.s.prepareForShutdown()
        if stats['status']['code']:
            return stats['status']['code'], stats['status']['message']
        return 0, ''

    def do_setLogLevel(self, args):
        level = int(args[0])
        assert len(args) == 1
        stats = self.s.setLogLevel(level)
        if stats['status']['code']:
            return stats['status']['code'], stats['status']['message']
        return 0, ''

    def do_setMOMPolicy(self, policyFile):
        stats = self.s.setMOMPolicy(policyFile)
        if stats['status']['code']:
            return stats['status']['code'], stats['status']['message']
        return 0, ''

    def do_setMOMPolicyParameters(self, args):
        # convert arguments in the form of key=value to a dictionary
        expand = lambda pair: (pair[0], eval(pair[1]))
        key_value_store = dict([expand(arg.split("=", 1))
                                for arg in args
                                if "=" in arg])
        stats = self.s.setMOMPolicyParameters(key_value_store)
        if stats['status']['code']:
            return stats['status']['code'], stats['status']['message']
        return 0, ''

    def do_setHaMaintenanceMode(self, args):
        assert len(args) == 2
        mode = args[0]
        enabled = utils.tobool(args[1])
        stats = self.s.setHaMaintenanceMode(mode, enabled)
        if stats['status']['code']:
            return stats['status']['code'], stats['status']['message']
        return 0, ''

    def do_getVmsInfo(self, args):
        spUUID = args[0]
        if len(args) >= 2:
            sdUUID = args[1]
        else:
            sdUUID = BLANK_UUID
        if len(args) >= 3:
            vmList = args[2].split(",")
        else:
            vmList = []
        infos = self.s.getVmsInfo(spUUID, sdUUID, vmList)
        if infos['status']['code'] != 0:
            return infos['status']['code'], infos['status']['message']
        else:
            message = ''
            for entry in infos['vmlist']:
                message += '\n' + '================================' + '\n'
                message += entry + '=' + infos['vmlist'][entry]
            if not message:
                message = 'No VMs found.'
            if isinstance(message, unicode):
                print message.encode('utf-8')
            else:
                print message

            return 0, ''

    def do_getVmsList(self, args):
        spUUID = args[0]
        if len(args) >= 2:
            sdUUID = args[1]
        else:
            sdUUID = BLANK_UUID

        vms = self.s.getVmsList(spUUID, sdUUID)
        if vms['status']['code'] != 0:
            return vms['status']['code'], vms['status']['message']
        else:
            message = ''
            for entry in vms['vmlist']:
                message += '\n' + '================================' + '\n'
                message += entry
            if not message:
                message = 'No VMs found.'
            print message
            return 0, ''

    def _eqSplit(self, args):
        d = {}
        for arg in args:
            kv = arg.split('=', 1)
            if len(kv) != 2:
                raise ValueError("Invalid argument: %s" % arg)
            k, v = kv
            d[k] = v
        return d

    def _splitDriveSpecItems(self, item):
        """
        BC is BC.
        """
        key, value = item.split(":", 1)
        if key in ("domain", "pool", "image", "volume"):
            key = "%sID" % key
        return key, value

    def _parseNestedSpec(self, spec):
        d = dict()

        if spec[0] != '{':
            raise Exception("_parseNestedSpec called with "
                            "non nested spec: '%s'" % spec)

        spec = spec[1:]
        while True:
            if not spec or '}' not in spec:
                raise Exception("nested spec not terminated "
                                "with '}' in '%s'" % spec)
            if spec[0] == '}':
                return d, spec[1:]

            # Split into first name + the rest
            if ':' not in spec:
                raise Exception("missing name value separator "
                                "':' in '%s'" % spec)
            name, spec = spec.split(":", 1)

            # Determine the value
            if spec[0] == '{':
                val, spec = self._parseNestedSpec(spec)
                d[name] = val
            else:
                # The value ends either with a ',' meaning it is followed by
                # another name:value pair, or with a '}' ending the spec
                i = 0
                while spec[i] != ',' and spec[i] != '}':
                    i = i + 1
                val = spec[:i]
                spec = spec[i:]
                d[name] = val

            # If there is a comma behind the value remove it before continuing
            if spec and spec[0] == ',':
                spec = spec[1:]

    def _parseDriveSpec(self, spec):
        """
        '{' or ',' means dict. (!)
        """
        if spec[0] == '{':
            val, spec = self._parseNestedSpec(spec)
            if spec:
                raise Exception("Trailing garbage after spec: '%s'" % spec)
            return val
        if ',' in spec:
            return dict(self._splitDriveSpecItems(item)
                        for item in spec.split(',') if item)
        return spec

    def do_setupNetworks(self, args):
        params = self._eqSplit(args)
        networks = self._parseDriveSpec(params.get('networks', '{}'))
        bondings = self._parseDriveSpec(params.get('bondings', '{}'))
        for k in ('networks', 'bondings'):
            if k in params:
                del params[k]
        params['connectivityCheck'] = params.get('connectivityCheck', 'False')
        for bond in bondings:
            if 'nics' in bondings[bond]:
                bondings[bond]['nics'] = bondings[bond]['nics'].split("+")
        status = self.s.setupNetworks(networks, bondings, params)
        return status['status']['code'], status['status']['message']

    def do_addNetwork(self, args):
        params = self._eqSplit(args)
        try:
            nics = filter(None, params['nics'].split(','))
        except:
            raise ValueError
        bridge = params.get('bridge', '')
        vlan = params.get('vlan', '')
        bond = params.get('bond', '')
        for k in ['bridge', 'vlan', 'bond', 'nics']:
            if k in params:
                del params[k]
        status = self.s.addNetwork(bridge, vlan, bond, nics, params)
        return status['status']['code'], status['status']['message']

    def do_editNetwork(self, args):
        params = self._eqSplit(args)
        try:
            nics = params['nics'].split(',')
        except:
            raise ValueError
        oldBridge = params.get('oldBridge', '')
        newBridge = params.get('newBridge', '')
        vlan = params.get('vlan', '')
        bond = params.get('bond', '')
        for k in ['oldBridge', 'newBridge', 'vlan', 'bond', 'nics']:
            if k in params:
                del params[k]
        status = self.s.editNetwork(oldBridge, newBridge, vlan, bond,
                                    nics, params)
        return status['status']['code'], status['status']['message']

    def do_delNetwork(self, args):
        params = self._eqSplit(args)
        try:
            nics = params['nics'].split(',')
        except:
            raise ValueError
        bridge = params.get('bridge', '')
        vlan = params.get('vlan', '')
        bond = params.get('bond', '')
        for k in ['bridge', 'vlan', 'bond', 'nics']:
            if k in params:
                del params[k]
        status = self.s.delNetwork(bridge, vlan, bond, nics, params)
        return status['status']['code'], status['status']['message']

    def do_setSafeNetworkConfig(self, args):
        status = self.s.setSafeNetworkConfig()
        return status['status']['code'], status['status']['message']

    def do_fenceNode(self, args):
        addr, port, agent, user, passwd, action = args[:6]
        status = self.s.fenceNode(addr, port, agent, user, passwd, action,
                                  *args[6:])
        if action == 'status' and 'power' in status:
            return status['status']['code'], status['power']
        return status['status']['code'], status['status']['message']

    def __image_status(self, imgUUID, res):
            if "imagestatus" in res and "message" in res:
                status = "OK"
                if res["imagestatus"]:
                    status = "ERROR"
                print("Image %s status %s: %s (%s)" %
                      (imgUUID, status, res["message"], res["imagestatus"]))
            if "badvols" in res:
                for v, err in res["badvols"].iteritems():
                    print "\tVolume %s is bad: %s" % (v, err)

    def __domain_status(self, sdUUID, res):
            if "domainstatus" in res and "message" in res:
                status = "OK"
                if res["domainstatus"]:
                    status = "ERROR"
                print("Domain %s status %s: %s (%s)" %
                      (sdUUID, status, res["message"], res["domainstatus"]))
            if "badimages" in res:
                for i in res["badimages"]:
                    print "\tImage %s is bad" % (i)
                    self.__image_status(i, res["badimages"][i])

    def __pool_status(self, spUUID, res):
            if "poolstatus" in res and "message" in res:
                status = "OK"
                if res["poolstatus"]:
                    status = "ERROR"
                print("Pool %s status %s: %s (%s)" %
                      (spUUID, status, res["message"], res["poolstatus"]))
            if "masterdomain":
                print "\tMaster domain is %s" % res["masterdomain"]
            if "spmhost":
                print "\tThe SPM host id is %s" % res["spmhost"]
            if "baddomains" in res:
                for d in res["baddomains"]:
                    print "\tDomain %s is bad:" % (d)
                    self.__domain_status(d, res["baddomains"][d])

    def repoStats(self, args):
        stats = self.s.repoStats()
        if stats['status']['code']:
            print "count not get repo stats"
            return int(stats['status']['code'])
        for d in stats:
            if d == "status":
                continue
            print 'Domain %s %s' % (d, str(stats[d]))
        return 0, ''

    def startMonitoringDomain(self, args):
        sdUUID, hostID = args
        status = self.s.startMonitoringDomain(sdUUID, hostID)
        return status['status']['code'], status['status']['message']

    def stopMonitoringDomain(self, args):
        sdUUID, = args
        status = self.s.stopMonitoringDomain(sdUUID)
        return status['status']['code'], status['status']['message']

    def snapshot(self, args):
        vmUUID, sdUUID, imgUUID, baseVolUUID, volUUID = args

        status = self.s.snapshot(vmUUID, [
            {'domainID': sdUUID,
             'imageID': imgUUID,
             'baseVolumeID': baseVolUUID,
             'volumeID': volUUID},
        ])

        return status['status']['code'], status['status']['message']

    def setBalloonTarget(self, args):
        vmId = args[0]
        target = int(args[1])
        response = self.s.setBalloonTarget(vmId, target)
        return response['status']['code'], response['status']['message']

    def diskReplicateStart(self, args):
        vmUUID, spUUID, sdUUID, imgUUID, volUUID, dstSdUUID = args

        status = self.s.diskReplicateStart(
            vmUUID,
            {'poolID': spUUID, 'domainID': sdUUID, 'imageID': imgUUID,
             'volumeID': volUUID},
            {'poolID': spUUID, 'domainID': dstSdUUID, 'imageID': imgUUID,
             'volumeID': volUUID})

        return status['status']['code'], status['status']['message']

    def diskReplicateFinish(self, args):
        vmUUID, spUUID, sdUUID, imgUUID, volUUID, dstSdUUID = args

        status = self.s.diskReplicateFinish(
            vmUUID,
            {'poolID': spUUID, 'domainID': sdUUID, 'imageID': imgUUID,
             'volumeID': volUUID},
            {'poolID': spUUID, 'domainID': dstSdUUID, 'imageID': imgUUID,
             'volumeID': volUUID})

        return status['status']['code'], status['status']['message']

    def diskSizeExtend(self, args):
        vmUUID, spUUID, sdUUID, imgUUID, volUUID, newSize = args

        status = self.s.diskSizeExtend(
            vmUUID, {
                'poolID': spUUID, 'domainID': sdUUID, 'imageID': imgUUID,
                'volumeID': volUUID, 'device': 'disk'
            }, newSize)

        if status['status']['code'] == 0:
            print "New disk size:", status.get('size', None)

        return status['status']['code'], status['status']['message']

if __name__ == '__main__':
    if _glusterEnabled:
        serv = ge.GlusterService()
    else:
        serv = service()
    commands = {
        'create': (serv.do_create,
                   ('<configFile> [parameter=value, parameter=value, ......]',
                    'Creates new machine with the paremeters given in the'
                    ' command line overriding the ones in the config file',
                    'Example with config file: vdsClient someServer create'
                    ' myVmConfigFile',
                    'Example with no file    : vdsClient someServer create'
                    ' /dev/null vmId=<uuid> memSize=256 '
                    'imageFile=someImage display=<vnc|qxl|qxlnc>',
                    'Parameters list: r=required, o=optional',
                    'r   vmId=<uuid> : Unique identification for the '
                    'created VM. Any additional operation on the VM must '
                    'refer to this ID',
                    'o   vmType=<qemu/kvm> : Virtual machine technology - '
                    'if not given kvm is default',
                    'o   kvmEnable=<true/false> : run in KVM enabled mode '
                    'or full emulation - default is according to the VDS '
                    'capabilities',
                    'r   memSize=<int> : Memory to allocate for this '
                    'machine',
                    'r   macAddr=<aa:bb:cc:dd:ee:ff> : MAC address of the '
                    'machine',
                    'r   display=<vnc|qxl|qxlnc> : send the machine '
                    'display to vnc, spice, or spice with no '
                    'image compression',
                    'o   drive=pool:poolID,domain:domainID,image:imageID,'
                    'volume:volumeID[,boot:true,format:cow] : disk image '
                    'by UUIDs',
                    'o   (deprecated) hda/b/c/d=<path> : Disk drive '
                    'images',
                    'o   floppy=<image> : Mount the specified Image as '
                    'floppy',
                    'o   cdrom=<path> : ISO image file to be mounted as '
                    'the powerup cdrom',
                    'o   boot=<c/d/n> : boot device - drive C or cdrom or '
                    'network',
                    'o   sysprepInf=/path/to/file: Launch with the '
                    'specified file as sysprep.inf in floppy',
                    # 'o   any parmeter=<any value> : parameter that is '
                    # 'not familiar is passed as is to the VM',
                    # '                               and displayed with '
                    # 'all other parameter. They can be used for '
                    # 'additional',
                    # '                               information the user '
                    # 'want to reserve with the machine'
                    'o   acpiEnable : If present will remove the default '
                    '-no-acpi switch',
                    'o   spiceSecureChannels : comma-separated list of '
                    'spice channel that will be encrypted',
                    'o   spiceMonitors : number of emulated screen heads',
                    'o   soundDevice : emulated sound device',
                    'o   launchPaused : If "true", start the VM paused',
                    'o   vmName : human-readable name of new VM',
                    'o   tabletEnable : If "true", enable tablet input',
                    'o   timeOffset : guest\'s start date, relative to '
                    'host\'s time, in seconds',
                    'o   smp : number of vcpus',
                    'o   smpCoresPerSocket, smpThreadsPerCore : vcpu '
                    'topology',
                    'o   keyboardLayout : language code of client '
                    'keyboard',
                    'o   cpuType : emulated cpu (with optional flags)',
                    'o   emulatedMachine : passed as qemu\'s -M',
                    'o   devices={name:val[, name:val, name:{name:val, '
                    'name:val}]} : add a fully specified device',
                    'o   cpuPinning={vcpuid:pinning} cpu pinning in '
                    'libvirt-like format. see '
                    'http://libvirt.org/formatdomain.html#elementsCPUTuning',
                    'o   numaTune={mode:val,nodeset:val} numa nodeset in '
                    'libvirt-like format. see '
                    'http://libvirt.org/formatdomain.html#elementsNUMATuning',
                    'o   guestNumaNodes={cpus:val,memory:val} cpus and memory '
                    'in libvirt-like format. See Guest NUMA topology section '
                    'in http://libvirt.org/formatdomain.html#elementsCPU'
                    )),
        'vmUpdateDevice': (serv.vmUpdateDevice,
                           ('<vmId> <devicespec>',
                            'Update a VM\'s device',
                            'Example: vmUpdateDevice xxxx deviceType=interface'
                            ' alias=net0 linkActive=false',
                            'devicespec list: r=required, '
                            'o=optional',
                            'r   devicetype: interface',
                            'o   network: network name - No chage if not '
                            'specified. Dummy bridge and link inactive if '
                            'empty string',
                            'o   linkActive: bool - No change if not '
                            'specified',
                            'r   alias: libvirt\'s vnic alias',
                            'o   portMirroring: net[,net] - Only networks to '
                            'mirror. No change if not specified, no mirroring'
                            'if empty list.'
                            )),
        'hotplugNic': (serv.hotplugNic,
                       ('<vmId> <nicspec>',
                        'Hotplug NIC to existing VM',
                        'nicspec parameters list: r=required, o=optional',
                        'r   device: bridge|sriov|vnlink|bridgeless.',
                        'r   network: network name',
                        'r   macAddr: mac address',
                        'r   nicModel: pv|rtl8139|e1000',
                        'o   bootOrder: <int>  - global boot order across '
                        'all bootable devices'
                        )),
        'hotunplugNic': (serv.hotunplugNic,
                         ('<vmId> <nicspec>',
                          'Hotunplug NIC from existing VM',
                          'nicspec parameters list: r=required, o=optional',
                          'r   device: bridge|sriov|vnlink|bridgeless.',
                          'r   network: network name',
                          'r   macAddr: mac address',
                          'r   nicModel: pv|rtl8139|e1000',
                          'o   bootOrder: <int>  - global boot order across '
                          'all bootable devices'
                          )),
        'hotplugDisk': (serv.hotplugDisk,
                        ('<vmId> <drivespec>',
                         'Hotplug disk to existing VM',
                         'drivespec parameters list: r=required, o=optional',
                         'r   iface:<ide|virtio> - Unique identification of '
                         'the existing VM.',
                         'r   index:<int> - disk index unique per interface '
                         'virtio|ide',
                         'r   [pool:UUID,domain:UUID,image:UUID,volume:UUID]|'
                         '[GUID:guid]|[UUID:uuid]',
                         'r   format: cow|raw',
                         'r   readonly: True|False   - default is False',
                         'r   propagateErrors: off|on   - default is off',
                         'o   bootOrder: <int>  - global boot order across '
                         'all bootable devices',
                         'o   shared: exclusive|shared|none',
                         'o   optional: True|False'
                         )),
        'hotunplugDisk': (serv.hotunplugDisk,
                          ('<vmId> <drivespec >',
                           'Hotunplug disk from existing VM',
                           'drivespec parameters list: r=required, o=optional',
                           'r   iface:<ide|virtio> - Unique identification of '
                           'the existing VM.',
                           'r   index:<int> - disk index unique per interface '
                           'virtio|ide',
                           'r   '
                           '[pool:UUID,domain:UUID,image:UUID,volume:UUID]|'
                           '[GUID:guid]|[UUID:uuid]',
                           'r   format: cow|raw',
                           'r   readonly: True|False   - default is False',
                           'r   propagateErrors: off|on   - default is off',
                           'o   bootOrder: <int>  - global boot order across '
                           'all bootable devices',
                           'o   shared: exclusive|shared|none',
                           'o   optional: True|False'
                           )),
        'changeCD': (serv.do_changeCD,
                     ('<vmId> <fileName|drivespec>',
                      'Changes the iso image of the cdrom'
                      )),
        'changeFloppy': (serv.do_changeFloppy,
                         ('<vmId> <fileName|drivespec>',
                          'Changes the image of the floppy drive'
                          )),
        'destroy': (serv.do_destroy,
                    ('<vmId>',
                     'Stops the emulation and destroys the virtual machine.'
                     ' This is not a shutdown.'
                     )),
        'shutdown': (serv.do_shutdown,
                     ('<vmId> <delay> <message> [reboot:bool] [timeout] '
                      '[force:bool]',
                      'Stops the emulation and graceful shutdown the virtual'
                      ' machine.',
                      'o reboot: if specified, reboot instead of shutdown'
                      ' (default False)',
                      'o timeout: number of seconds to wait before proceeding '
                      'to next shutdown/reboot method',
                      'o force: if specified, forcefuly reboot/shutdown'
                      ' after all graceful methods fail (default False)'
                      )),
        'list': (serv.do_list,
                 ('[view] [vms:vmId1,vmId2]',
                  'Lists all available machines on the specified server.',
                  "Optional vms list, should start with 'vms:' and follow with"
                  " 'vmId1,vmId2,...'",
                  'Optional views:',
                  '    "long"   all available configuration info (Default).',
                  '    "table"  table output with the fields: vmId, vmName, '
                  'Status and IP.',
                  '    "ids"    all vmIds.'
                  )),
        'pause': (serv.do_pause,
                  ('<vmId>',
                   'Pauses the execution of the virtual machine without '
                   'termination'
                   )),
        'continue': (serv.do_continue,
                     ('<vmId>',
                      'Continues execution after of a paused machine'
                      )),
        'reset': (serv.do_reset,
                  ('<vmId>',
                   'Sends reset signal to the vm'
                   )),
        'setVmTicket': (serv.do_setVmTicket,
                        ('<vmId> <password> <sec> [disconnect|keep|fail], '
                            '[params={}] [-- auth=]',
                         'Set the password to the vm display for the next '
                         '<sec> seconds.',
                         'Optional argument instructs spice regarding '
                         'currently-connected client.',
                         'Optional additional parameters in dictionary format,'
                         ' name:value,name:value',
                         'These parameters can only be passed after --:',
                         'auth=',
                         'If auth argument is provided, password will be '
                         'ignored (yet has to be specified, ie -)'
                         )),
        'migrate': (serv.do_migrate,
                    ('vmId=<id> method=<offline|online> src=<host[:port]> '
                     'dst=<host[:port]>  dstqemu=<host>',
                     'Migrate a desktop from src machine to dst host using '
                     'the specified ports and an optional address for '
                     'migration data traffic.'
                     )),
        'migrateStatus': (serv.do_mStat,
                          ('<vmId>',
                           'Check the progress of current outgoing migration'
                           )),
        'migrateCancel': (serv.do_mCancel,
                          ('<vmId>',
                           '(not implemented) cancel machine migration'
                           )),
        'sendkeys': (serv.do_sendkeys,
                     ('<vmId> <key1> ...... <keyN>',
                      'Send the key sequence to the vm'
                      )),
        'getVdsCapabilities': (serv.do_getCap,
                               ('',
                                'Get Capabilities info of the VDS'
                                )),
        'getVdsCaps': (serv.do_getCap,
                       ('',
                        'Get Capabilities info of the VDS'
                        )),
        'getVdsHardwareInfo': (serv.do_getHardware,
                               ('',
                                'Get hardware info of the VDS'
                                )),
        'getVdsStats': (serv.do_getVdsStats,
                        ('',
                         'Get Statistics info on the VDS'
                         )),
        'getVmStats': (serv.do_getVmStats,
                       ('<vmId>',
                        'Get Statistics info on the VM'
                        )),
        'getAllVmStats': (serv.do_getAllVmStats,
                          ('',
                           'Get Statistics info for all existing VMs'
                           )),
        'getVGList': (serv.getVGList,
                      ('storageType',
                       'List of all VGs.'
                       )),
        'getDeviceList': (serv.getDeviceList,
                          ('[storageType]',
                           'List of all block devices (optionally - matching '
                           'storageType).'
                           )),
        'getDevicesVisibility': (serv.getDevicesVisibility,
                                 ('<devlist>',
                                  'Get visibility of each device listed'
                                  )),
        'getDiskAlignment': (serv.getDiskAlignment,
                             ('[<vmId> <poolId> <domId> <imgId> <volId>]',
                              '[<vmId> <GUID>]',
                              'Get alignment of each partition on the device'
                              )),
        'getVGInfo': (serv.getVGInfo,
                      ('<vgUUID>',
                       'Get info of VG'
                       )),
        'createVG': (serv.createVG,
                     ('<sdUUID> <devlist> [force]',
                      'Create a new VG from devices devlist (list of dev '
                      'GUIDs)'
                      )),
        'removeVG': (serv.removeVG,
                     ('<vgUUID>',
                      'remove the VG identified by its UUID'
                      )),
        'extendStorageDomain': (serv.extendStorageDomain,
                                ('<sdUUID> <spUUID> <devlist>',
                                 'Extend the Storage Domain by adding devices'
                                 ' devlist (list of dev GUIDs)'
                                 )),
        'discoverST': (serv.discoverST,
                       ('ip[:port] [[username password] [auth=]]',
                        'Discover the available iSCSI targetnames on a '
                        'specified iSCSI portal',
                        'If auth argument is provided, password will be '
                        'ignored (yet has to be specified, ie -)'
                        )),
        'cleanupUnusedConnections': (serv.cleanupUnusedConnections,
                                     ('',
                                      'Clean up unused iSCSI storage '
                                      'connections'
                                      )),
        'connectStorageServer': (serv.connectStorageServer,
                                 ('<server type> <spUUID> <conList (id=...,'
                                  'connection=server:/export_path,portal=...,'
                                  'port=...,iqn=...,user=...,'
                                  'password|auth=...'
                                  '[,initiatorName=...])>',
                                  'Connect to a storage low level entity '
                                  '(server)',
                                  'password= can be omitted if auth= is '
                                  'specified, if both specified, auth= takes '
                                  'precedence.'
                                  )),
        'validateStorageServerConnection':
        (serv.validateStorageServerConnection,
         ('<server type> <spUUID> <conList (id=...,'
          'connection=server:/export_path,portal=...,port=...,iqn=...,'
          'user=...,password|auth=...[,initiatorName=...])>',
          'Validate that we can connect to a storage server',
          'password= can be omitted if auth= is specified, if both specified, '
          'auth= takes precedence.'
          )),
        'disconnectStorageServer': (serv.disconnectStorageServer,
                                    ('<server type> <spUUID> <conList (id=...,'
                                     'connection=server:/export_path,'
                                     'portal=...,port=...,iqn=...,user=...,'
                                     'password|auth=...[,initiatorName=...])>',
                                     'Disconnect from a storage low level '
                                     'entity (server)',
                                     'password= can be omitted if auth= is '
                                     'specified, if both specified, auth= '
                                     'takes precedence.'
                                     )),
        'spmStart': (serv.spmStart,
                     ('<spUUID> <prevID> <prevLVER> <recoveryMode> '
                      '<scsiFencing> <maxHostID> <version>',
                      'Start SPM functionality',
                      'Parameters scsiFencing and recoveryMode are ignored '
                      '(maintained only for the command line backward '
                      'compatibility)'
                      )),
        'spmStop': (serv.spmStop,
                    ('<spUUID>',
                     'Stop SPM functionality'
                     )),
        'getSpmStatus': (serv.getSpmStatus,
                         ('<spUUID>',
                          'Get SPM status'
                          )),
        'acquireDomainLock': (serv.acquireDomainLock,
                              ('<spUUID> <sdUUID>',
                               'acquire storage domain lock'
                               )),
        'releaseDomainLock': (serv.releaseDomainLock,
                              ('<spUUID> <sdUUID>',
                               'release storage domain lock'
                               )),
        'fenceSpmStorage': (serv.fenceSpmStorage,
                            ('<spUUID> <prevID> <prevLVER> ',
                             'fence SPM storage state'
                             )),
        'updateVM': (serv.updateVM,
                     ("<spUUID> <vmList> ('vm'=vmUUID,'ovf'='...','"
                      "imglist'='imgUUID1+imgUUID2+...') [sdUUID]",
                      'Update VM on pool or Backup domain'
                      )),
        'upgradeStoragePool': (serv.upgradeStoragePool,
                               ("<spUUID> <targetVersion>",
                                'Upgrade a pool to a new version (Requires a '
                                'running SPM)'
                                )),
        'removeVM': (serv.removeVM,
                     ('<spUUID> <vmUUID> [sdUUID]',
                      'Remove VM from pool or Backup domain'
                      )),
        'reconstructMaster': (serv.reconstructMaster,
                              ('<spUUID> <poolName> <masterDom> '
                               '<domDict>({sdUUID1=status,sdUUID2=status,...})'
                               ' <masterVersion>, [<lockPolicy> '
                               '<lockRenewalIntervalSec> <leaseTimeSec> '
                               '<ioOpTimeoutSec> <leaseRetries>]',
                               'Reconstruct master domain'
                               )),
        'createStoragePool': (serv.createStoragePool,
                              ('<storage type> <spUUID> <poolName> <masterDom>'
                               ' <domList>(sdUUID1,sdUUID2,...) '
                               '<masterVersion>, [<lockPolicy> '
                               '<lockRenewalIntervalSec> <leaseTimeSec> '
                               '<ioOpTimeoutSec> <leaseRetries>]',
                               'Create new storage pool with single/multiple '
                               'image data domain'
                               )),
        'destroyStoragePool': (serv.destroyStoragePool,
                               ('<spUUID> <id> <scsi-key>',
                                'Destroy storage pool',
                                'Parameter scsi-key is ignored (maintained '
                                'only for the command line backward '
                                'compatibility)'
                                )),
        'connectStoragePool': (serv.connectStoragePool, (
            '<spUUID> <id> <scsi-key> [masterUUID] [masterVer] '
            '[<domDict>({sdUUID1=status,sdUUID2=status,...})]',
            'Connect a Host to specific storage pool.',
            'Parameters list: r=required, o=optional',
            'o   scsi-key : ignored (maintained only for the command line '
            'backward compatibility)',
            'o   domDict : provides a map of domains (and status) that '
            'are part of the pool, this selects the pool metadata memory '
            'backend',
        )),
        'disconnectStoragePool': (serv.disconnectStoragePool,
                                  ('<spUUID> <id> <scsi-key>',
                                   'Disconnect a Host from the specific '
                                   'storage pool',
                                   'Parameter scsi-key is ignored '
                                   '(maintained only for the command line '
                                   'backward compatibility)'
                                   )),
        'refreshStoragePool': (serv.refreshStoragePool,
                               ('<spUUID> <masterDom> <masterVersion>',
                                'Refresh storage pool'
                                )),
        'setStoragePoolDescription': (serv.setStoragePoolDescription,
                                      ('<spUUID> <descr>',
                                       'Set storage pool description'
                                       )),
        'getStoragePoolInfo': (serv.getStoragePoolInfo,
                               ('<spUUID>',
                                'Get storage pool info'
                                )),
        'createStorageDomain': (serv.createStorageDomain,
                                ('<storage type> <domain UUID> <domain name> '
                                 '<param> <domType> <version>',
                                 'Creates new storage domain'
                                 )),
        'setStorageDomainDescription': (serv.setStorageDomainDescription,
                                        ('<domain UUID> <descr>',
                                         'Set storage domain description'
                                         )),
        'validateStorageDomain': (serv.validateStorageDomain,
                                  ('<domain UUID>',
                                   'Validate storage domain'
                                   )),
        'activateStorageDomain': (serv.activateStorageDomain,
                                  ('<domain UUID> <pool UUID>',
                                   'Activate a storage domain that is already '
                                   'a member in a storage pool.'
                                   )),
        'deactivateStorageDomain': (serv.deactivateStorageDomain,
                                    ('<domain UUID> <pool UUID> <new master '
                                     'domain UUID> <masterVer>',
                                     'Deactivate a storage domain. '
                                     )),
        'attachStorageDomain': (serv.attachStorageDomain,
                                ('<domain UUID> <pool UUID>',
                                 'Attach a storage domain to a storage pool.'
                                 )),
        'detachStorageDomain': (serv.detachStorageDomain,
                                ('<domain UUID> <pool UUID> <new master domain'
                                 ' UUID> <masterVer>',
                                 'Detach a storage domain from a storage pool.'
                                 )),
        'forcedDetachStorageDomain': (serv.forcedDetachStorageDomain,
                                      ('<domain UUID> <pool UUID>',
                                       'Forced detach a storage domain from a '
                                       'storage pool.'
                                       )),
        'formatStorageDomain': (serv.formatStorageDomain,
                                ('<domain UUID> [<autoDetach>]',
                                 'Format detached storage domain.'
                                 )),
        'getStorageDomainInfo': (serv.getStorageDomainInfo,
                                 ('<domain UUID>',
                                  'Get storage domain info.'
                                  )),
        'getStorageDomainStats': (serv.getStorageDomainStats,
                                  ('<domain UUID>',
                                   'Get storage domain statistics.'
                                   )),
        'getStorageDomainsList': (serv.getStorageDomainsList,
                                  ('<pool UUID>',
                                   'Get storage domains list of pool or all '
                                   'domains if pool omitted.'
                                   )),
        'createVolume': (serv.createVolume,
                         ('<sdUUID> <spUUID> <imgUUID> <size> <volFormat> '
                          '<preallocate> <diskType> <newVolUUID> <descr> '
                          '<srcImgUUID> <srcVolUUID>',
                          'Creates new volume or snapshot'
                          )),
        'extendVolumeSize': (serv.extendVolumeSize, (
            '<spUUID> <sdUUID> <imgUUID> <volUUID> <newSize>',
            'Extend the volume size (virtual disk size seen by the guest).',
        )),
        'getVolumePath': (serv.getVolumePath,
                          ('<sdUUID> <spUUID> <imgUUID> <volume uuid>',
                           'Returns the path to the requested uuid'
                           )),
        'setVolumeDescription': (serv.setVolumeDescription,
                                 ('<sdUUID> <spUUID> <imgUUID> <volUUID> '
                                  '<Description>',
                                  'Sets a new description to the volume'
                                  )),
        'setVolumeLegality': (serv.setVolumeLegality,
                              ('<sdUUID> <spUUID> <imgUUID> <volUUID> '
                               '<Legality>',
                               'Set volume legality (ILLEGAL/LEGAL).'
                               )),
        'deleteVolume': (serv.deleteVolume,
                         ('<sdUUID> <spUUID> <imgUUID> <volUUID>,...,<volUUID>'
                          ' <postZero> [<force>]',
                          'Deletes an volume if its a leaf. Else returns error'
                          )),
        'deleteVolumeByDescr': (serv.deleteVolumeByDescr,
                                ('<part of description> <sdUUID> <spUUID> '
                                 '<imgUUID>',
                                 'Deletes list of volumes(only leafs) '
                                 'according to their description'
                                 )),
        'getVolumeInfo': (serv.getVolumeInfo,
                          ('<sdUUID> <spUUID> <imgUUID> <volUUID>',
                           'Returns all the volume details'
                           )),
        'getParent': (serv.getParent,
                      ('<sdUUID> <spUUID> <imgUUID> <Disk Image uuid>',
                       'Returns the parent of the volume. Error if no parent'
                       ' exists'
                       )),
        'getVolumesList': (serv.getVolumesList,
                           ('<sdUUID> <spUUID> [imgUUID]',
                            'Returns list of volumes of imgUUID or sdUUID if '
                            'imgUUID absent'
                            )),
        'getVolumeSize': (serv.getVolumeSize,
                          ('<sdUUID> <spUUID> <imgUUID> <volUUID>',
                           'Returns the apparent size and the true size of the'
                           ' volume (in bytes)'
                           )),
        'getFileStats': (serv.getFileStats,
                         ('<sdUUID> [pattern][caseSensitive]',
                          'Returns files statistics from ISO domain'
                          )),
        'getIsoList': (serv.getIsoList,
                       ('<spUUID>',
                        'Returns list of all .iso images in ISO domain'
                        )),
        'getFloppyList': (serv.getFloppyList,
                          ('<spUUID>',
                           'Returns list of all .vfd images in ISO domain'
                           )),
        'getImagesList': (serv.getImagesList,
                          ('<sdUUID>',
                           'Get list of all images of specific domain'
                           )),
        'getImageDomainsList': (serv.getImageDomainsList,
                                ('<spUUID> <imgUUID> [datadomain=True]',
                                 'Get list of all data domains in the pool '
                                 'that contains imgUUID'
                                 )),
        'getConnectedStoragePoolsList': (serv.getConnectedStoragePoolsList,
                                         ('',
                                          'Get storage pools list'
                                          )),
        'getTaskInfo': (serv.getTaskInfo,
                        ('<TaskID>',
                         'get async task info'
                         )),
        'getAllTasksInfo': (serv.getAllTasksInfo,
                            ('',
                             'get info of all async tasks'
                             )),
        'getTaskStatus': (serv.getTaskStatus,
                          ('<TaskID>',
                           'get task status'
                           )),
        'getAllTasksStatuses': (serv.getAllTasksStatuses,
                                ('',
                                 'list statuses of all async tasks'
                                 )),
        'getAllTasks': (serv.getAllTasks,
                        ('[tags=\'\']',
                         'get status and information for all async tasks'
                         )),
        'stopTask': (serv.stopTask,
                     ('<TaskID>',
                      'stop async task'
                      )),
        'clearTask': (serv.clearTask,
                      ('<TaskID>',
                       'clear async task'
                       )),
        'revertTask': (serv.revertTask,
                       ('<TaskID>',
                        'revert async task'
                        )),
        'prepareForShutdown': (serv.prepareForShutdown,
                               ('', '')),
        'setLogLevel': (serv.do_setLogLevel,
                        ('<level> [logName][,logName]...', 'set log verbosity'
                         ' level (10=DEBUG, 50=CRITICAL'
                         )),
        'setMOMPolicy': (serv.do_setMOMPolicy,
                         ('<policyfile>', 'set MOM policy')),
        'setMOMPolicyParameters': (serv.do_setMOMPolicyParameters,
                                   ('key=python_code [key=python_code] ...',
                                    'set variables for MOM policy fine '
                                    'tuning')),
        'setHaMaintenanceMode': (serv.do_setHaMaintenanceMode,
                                 ('<type = global/local>'
                                  ' <enabled = true/false>',
                                  'Enable or disable Hosted Engine HA'
                                  ' maintenance')),
        'deleteImage': (serv.deleteImage,
                        ('<sdUUID> <spUUID> <imgUUID> [<postZero>] [<force>]',
                         'Delete Image folder with all volumes.',
                         )),
        'moveImage': (serv.moveImage,
                      ('<spUUID> <srcDomUUID> <dstDomUUID> <imgUUID> <vmUUID>'
                       ' <op = COPY_OP/MOVE_OP> [<postZero>] [ <force>]',
                       'Move/Copy image between storage domains within same '
                       'storage pool'
                       )),
        'sparsifyImage': (serv.sparsifyImage,
                          ('<spUUID> <tmpSdUUID> <tmpImgUUID> <tmpVolUUID> '
                           '<dstSdUUID> <dstImgUUID> <dstVolUUID>',
                           'Reduce the size of a sparse image by converting '
                           'free space on image to free space on storage '
                           'domain using virt-sparsify'
                           )),
        'cloneImageStructure': (serv.cloneImageStructure,
                                ('<spUUID> <sdUUID> <imgUUID> <dstSdUUID>',
                                 'Clone an image structure from a source '
                                 'domain to a destination domain within the '
                                 'same pool.'
                                 )),
        'syncImageData': (serv.syncImageData,
                          ('<spUUID> <sdUUID> <imgUUID> <dstSdUUID> '
                           '<syncType>',
                           'Synchronize image data between storage domains '
                           'within same pool.'
                           )),
        'uploadImage': (serv.uploadImage, (
            '<methodArgs> <spUUID> <sdUUID> <imgUUID> [<volUUID>]',
            'Upload an image to a remote endpoint using the specified'
            'methodArgs.'
        )),
        'downloadImage': (serv.downloadImage, (
            '<methodArgs> <spUUID> <sdUUID> <imgUUID> [<volUUID>]',
            'Download an image from a remote endpoint using the specified',
            'methodArgs.'
        )),
        'prepareImage': (serv.prepareImage, (
            '<spUUID> <sdUUID> <imgUUID> [<volUUID>]',
            'Prepare an image, making the needed volumes available.'
        )),
        'teardownImage': (serv.teardownImage, (
            '<spUUID> <sdUUID> <imgUUID> [<volUUID>]',
            'Teardown an image, releasing the prepared volumes.'
        )),
        'moveMultiImage': (serv.moveMultiImage,
                           ('<spUUID> <srcDomUUID> <dstDomUUID> '
                            '<imgList>({imgUUID=postzero,'
                            'imgUUID=postzero,...}) <vmUUID> [<force>]',
                            'Move multiple images between storage domains '
                            'within same storage pool'
                            )),
        'copyImage': (serv.copyImage,
                      ('<sdUUID> <spUUID> <vmUUID> <srcImgUUID> <srcVolUUID> '
                       '<dstImgUUID> <dstVolUUID> <dstDescr> <dstSdUUID> '
                       '<volType> <volFormat> <preallocate> [<postZero>] '
                       '[<force>]',
                       'Create new template/volume from VM.',
                       'Do it by collapse and copy the whole chain '
                       '(baseVolUUID->srcVolUUID)'
                       )),
        'mergeSnapshots': (serv.mergeSnapshots,
                           ('<sdUUID> <spUUID> <vmUUID> <imgUUID> <Ancestor '
                            'Image uuid> <Successor Image uuid> [<postZero>]',
                            'Merge images from successor to ancestor.',
                            'The result is a image named as successor image '
                            'and contents the data of whole successor->'
                            'ancestor chain'
                            )),
        'desktopLogin': (serv.desktopLogin,
                         ('<vmId> <domain> <user> <password> [auth=]',
                          'Login to vmId desktop using the supplied '
                          'credentials',
                          'If auth argument is provided, password will be '
                          'ignored (yet has to be specified, ie -)'
                          )),
        'desktopLogoff': (serv.desktopLogoff,
                          ('<vmId> <force>',
                           'Lock user session. force should be set to '
                           'true/false'
                           )),
        'desktopLock': (serv.desktopLock,
                        ('<vmId>',
                         'Logoff current user'
                         )),
        'sendHcCmd': (serv.sendHcCmd,
                      ('<vmId> <message>',
                       'Sends a message to a specific VM through Hypercall '
                       'channel'
                       )),
        'hibernate': (serv.hibernate,
                      ('<vmId> <hiberVolHandle>',
                       'Hibernates the desktop'
                       )),
        'monitorCommand': (serv.monitorCommand,
                           ('<vmId> <string>',
                            'Send a string containing monitor command to the '
                            'desktop'
                            )),
        'getVmsInfo': (serv.do_getVmsInfo,
                       ('<spUUID> [<sdUUID> [vmList](vmId1,vmId2,...)]',
                        'Return info of VMs from the pool or a backup domain '
                        'if its sdUUID is given. If vmList is also given, '
                        'return info for these VMs only.'
                        )),
        'getVmsList': (serv.do_getVmsList,
                       ('<spUUID> [sdUUID]',
                        'Get list of VMs from the pool or domain if sdUUID '
                        'given. Run only from the SPM.'
                        )),
        'setupNetworks': (serv.do_setupNetworks,
                          ('[connectivityCheck=False(default)|True] '
                           '[connectivityTimeout=<seconds>] '
                           '[<option>=<value>] '
                           '[networks=\'{<bridge>:{nic:<nic>,vlan:<number>,'
                           'bonding:<bond>,...}}\'] '
                           '[bondings=\'{<bond>:{nics:<nic>[+<nic>],..}}\']',
                           'Setup new configuration of multiple networks and '
                           'bonds.'
                           )),
        'addNetwork': (serv.do_addNetwork,
                       ('bridge=<bridge> [vlan=<number>] [bond=<bond>] '
                        'nics=nic[,nic]',
                        'Add a new network to this vds.'
                        )),
        'delNetwork': (serv.do_delNetwork,
                       ('bridge=<bridge> [vlan=<number>] [bond=<bond>] '
                        'nics=nic[,nic]',
                        'Remove a network (and parts thereof) from this vds.'
                        )),
        'editNetwork': (serv.do_editNetwork,
                        ('oldBridge=<bridge> newBridge=<bridge> '
                         '[vlan=<number>] '
                         '[bond=<bond>] nics=nic[,nic]',
                         'Replace a network with a new one.'
                         )),
        'setSafeNetworkConfig': (serv.do_setSafeNetworkConfig,
                                 ('',
                                  'declare current network configuration as '
                                  '"safe"'
                                  )),
        'fenceNode': (serv.do_fenceNode,
                      ('<addr> <port> <agent> <user> <passwd> <action> '
                       '[<secure> [<options>]] \n\t<action> is one of '
                       '(status, on, off, reboot),\n\t<agent> is one of '
                       '(rsa, ilo, ipmilan, drac5, etc)\n\t<secure> '
                       '(true|false) may be passed to some agents',
                       'send a fencing command to a remote node'
                       )),
        'repoStats': (serv.repoStats,
                      ('',
                       'Get the health status of the monitored domains'
                       )),
        'startMonitoringDomain': (serv.startMonitoringDomain,
                                  ('<sdUUID> <hostID>',
                                   'Start SD: sdUUID monitoring with hostID'
                                   )),
        'stopMonitoringDomain': (serv.stopMonitoringDomain,
                                 ('<sdUUID>',
                                  'Stop monitoring SD: sdUUID'
                                  )),
        'snapshot': (serv.snapshot,
                     ('<vmId> <sdUUID> <imgUUID> <baseVolUUID> <volUUID>',
                      'Take a live snapshot'
                      )),
        'setBalloonTarget': (serv.setBalloonTarget,
                             ('<vmId> <target>',
                              "Set VM's balloon target"
                              )),
        'diskReplicateStart': (serv.diskReplicateStart,
                               ('<vmId> <spUUID> <sdUUID> <imgUUID> <volUUID> '
                                '<dstSdUUID>',
                                'Start live replication to the destination '
                                'domain'
                                )),
        'diskReplicateFinish': (serv.diskReplicateFinish,
                                ('<vmId> <spUUID> <sdUUID> <imgUUID> <volUUID>'
                                 ' <dstSdUUID>',
                                 'Finish live replication to the destination '
                                 'domain'
                                 )),
        'diskSizeExtend': (
            serv.diskSizeExtend, (
                '<vmId> <spUUID> <sdUUID> <imgUUID> <volUUID> <newSize>',
                'Extends the virtual size of a disk'
            )),
        'setNumberOfCpus': (
            serv.setNumberOfCpus, (
                '<vmId> <numberOfCpus>',
                'set the number of cpus for a running VM'
            )),
        'merge': (
            serv.merge, (
                '<vmId> <driveSpec> <baseVolId> <topVolId> [<bandwidth> '
                '<jobId>',
                'Live merge disk snapshots between a base volume and a top '
                'volume into the base volume.  If specified, limit bandwidth',
                'to <bandwidth> MB/s and apply <jobID> to the operation for',
                'tracking purposes.'
            )),
        'updateVmPolicy': (
            serv.updateVmPolicy, (
                '<vmId> <vcpuLimit>',
                'set SLA parameter for a VM'
            )),
    }
    if _glusterEnabled:
        commands.update(ge.getGlusterCmdDict(serv))

    try:
        opts, args = getopt.getopt(sys.argv[1:], "hmso", ["help", "methods",
                                                          "SSL", "truststore=",
                                                          "oneliner"])

        for o, v in opts:
            if o == "-h" or o == "--help":
                usage(commands)
                sys.exit(0)
            if o == "-m" or o == "--methods":
                usage(commands, False)
                sys.exit(0)
            if o == "-s" or o == "--SSL":
                serv.useSSL = True
            if o == "--truststore":
                serv.truststore = v
            if o == '-o' or o == '--oneliner':
                serv.pretty = False
        if len(args) < 2:
            raise Exception("Need at least two arguments")
        server, command = args[0:2]
        if command not in commands:
            raise Exception("Unknown command")
        hostPort = vdscli.cannonizeHostPort(server)

    except SystemExit as status:
        sys.exit(status)
    except Exception as e:
        print "ERROR - %s" % (e)
        usage(commands)
        sys.exit(-1)

    try:
        serv.do_connect(hostPort)
        try:
            commandArgs = args[2:]
        except:
            commandArgs = []
        code, message = commands[command][0](commandArgs)
        if code != 0:
            code = 1
        print message
        sys.exit(code)
    except (TypeError, IndexError, ValueError, AssertionError) as e:
        print "Error using command:", e, "\n"
        print command
        for line in commands[command][1]:
            print '\t' + line
        sys.exit(-1)
    except SystemExit as status:
        sys.exit(status)
    except socket.error as e:
        if e[0] == 111:
            print "Connection to %s refused" % hostPort
        else:
            traceback.print_exc()
        sys.exit(-1)
    except:
        traceback.print_exc()
        sys.exit(-1)
