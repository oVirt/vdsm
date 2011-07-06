#!/usr/bin/python

import sys
import getopt
import traceback
import xmlrpclib
import re
import socket
import pprint as pp

import vdscli

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

def validateArgTypes(args, conv, requireAllArgs = False):
    if len(args) > len(conv) or requireAllArgs and len(args) < len(conv):
        raise ValueError("Wrong number of arguments provided, "
                        "expecting %d got %d" % (len(conv), len(args)))

    for i in range(len(args)):
        args[i] = conv[i](args[i])

def fmt3(num):
    for x in ['','KB','MB','GB','TB']:
        if num < 1024:
            return "%3.1f%s" % (num, x)
        num /= 1024

def usage(cmd, full=True):
    print "Usage:  vdsClient <server> [OPTIONS] <command> [Command parameters]"
    print "\nOptions"
    print "-h\tDisplay this help"
    print "-m\tList supported methods and their params (Short help)"
    print "-s [--truststore path]\tConnect to server with SSL.\n\tIf truststore path is not specified, use defaults."
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

def printDict(dict):
    keys = dict.keys()
    keys.sort()
    for element in keys:
        print "\t%s = %s" % (element, dict[element])

def printStats(list):
    for conf in list:
        printConf(conf)

class service:
    def __init__(self):
        self.useSSL = False
        self.truststore = None

    def do_connect(self, server, port):
        self.s = vdscli.connect(server + ':' + port, self.useSSL, self.truststore)

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
                printDict(response['info'])
            else:
                printDict(response['status'])
        sys.exit(response['status']['code'])

    def do_create(self, args):
        params={}
        confFile = open(args[0])
        for line in confFile.readlines():
            line = re.sub("\s+", '', line)
            line = re.sub("\#.*", '', line)
            if '=' in line:
                param,value = line.split("=")
                params[param] = value
        drives = []
        if len(args) > 1:
            for line in args[1:]:
                if '=' in line:
                    param,value = line.split("=",1)
                    if param == 'drive':
                        drives += [self._parseDriveSpec(value)]
                    elif param in ('cdrom', 'floppy'):
                        value = self._parseDriveSpec(value)
                    if param.startswith('custom_'):
                        if not 'custom' in params: params['custom'] = {}
                        params['custom'][param[7:]] = value
                    else:
                        params[param] = value
                else:
                    params[line.strip()] = ''
        if drives:
            params['drives'] = drives
        ##Backward competability for vdsClient users
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

    def do_changeCD(self, args):
        vmId = args[0]
        file = self._parseDriveSpec(args[1])
        return self.ExecAndExit(self.s.changeCD(vmId, file))

    def do_changeFloppy(self, args):
        vmId = args[0]
        file = self._parseDriveSpec(args[1])
        return self.ExecAndExit(self.s.changeFloppy(vmId, file))

    def do_list(self, args):
        table=False
        if len(args):
            if args[0] == 'table':
                table=True
        response = self.s.list(True)
        if response['status']['code'] != 0:
            print response['status']['message']
        else:
            if table:
                allStats = {}
                for s in self.s.getAllVmStats()['statsList']:
                    allStats[s['vmId']] = s
            for conf in response['vmList']:
                if table:
                    id = conf['vmId']
                    status = conf['status']
                    if allStats[id].get('monitorResponse') == '-1':
                        status += '*'
                    print "%-36s %6s  %-20s %-20s %-20s" % (id,
                        conf.get('pid', 'none'),
                        conf.get('vmName', '<< NO NAME >>'),
                        status, allStats[id].get('guestIPs', '') )
                else:
                    if 'sysprepInf' in conf:
                        conf['sysprepInf'] = '<<exists>>'
                    printConf(conf)
        sys.exit(response['status']['code'])


    def do_listNames(self, args):
        response = self.s.list()
        if response['status']['code'] != 0:
            print response['status']['message']
        else:
            names = []
            for conf in response['vmList']:
                names.append(conf['vmId'])
            names.sort()
            if names:
                print '\n'.join(names)
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
        vmId, timeout, message = args
        response = self.s.shutdown(vmId, timeout, message)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def do_setVmTicket(self, args):
        if len(args) == 4:
            vmId, otp64, secs, connAct = args
        else:
            vmId, otp64, secs = args
            connAct = 'disconnect'
        return self.ExecAndExit(self.s.setVmTicket(vmId, otp64, secs, connAct))

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
        vmId = args[0]
        response = self.s.hibernate(vmId)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def do_migrate(self, args):
        params = {}
        if len(args) > 0:
            for line in args:
                param,value = line.split("=")
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
            print response['status']['message'] + ' ' + str(response['progress']) +'%'
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

    def do_getVdsStats(self, args):
        return self.ExecAndExit(self.s.getVdsStats())

    def do_getVmStats(self, args):
        vmId = args[0]
        if len(args) > 1 :
            return self.ExecAndExit(self.s.getVmStats(vmId), args[1])
        else:
            return self.ExecAndExit(self.s.getVmStats(vmId))

    def do_getAllVmStats(self, args):
        return self.ExecAndExit(self.s.getAllVmStats())

    def desktopLogin(self, args):
        vmId, domain, user, password = tuple(args)
        response = self.s.desktopLogin(vmId, domain, user, password)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def desktopLock (self, args):
        vmId=args[0]
        response = self.s.desktopLock(vmId)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def desktopLogoff (self, args):
        vmId, force = tuple(args)
        response = self.s.desktopLogoff(vmId, force)
        print response['status']['message']
        sys.exit(response['status']['code'])

    def sendHcCmd (self, args):
        vmId, message = tuple(args)
        response = self.s.sendHcCmdToDesktop(vmId, message)
        print response['status']['message']
        sys.exit(response['status']['code'])

######## IRS methods ####################
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
        if len(args)>0:
            spUUID = args[0]
        else:
            spUUID = BLANK_UUID
        list = self.s.getStorageDomainsList(spUUID)
        if list['status']['code']:
            return list['status']['code'], list['status']['message']
        for entry in list['domlist']:
            print entry
        return 0, ''

    def getDeviceList(self, args):
        list = self.s.getDeviceList(*args)
        if list['status']['code']:
            return list['status']['code'], list['status']['message']
        pp.pprint(list['devList'])
        return 0, ''

    def getDeviceInfo(self, args):
        guid = args[0]
        info = self.s.getDeviceInfo(guid)
        if info['status']['code']:
            return info['status']['code'], info['status']['message']
        pp.pprint(info["info"])
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
            list = self.s.getVGList(storageType)
        else:
            list = self.s.getVGList()

        if list['status']['code']:
            return list['status']['code'], list['status']['message']
        for entry in list['vglist']:
            print '============================'
            for element in entry.keys():
                print "%s = %s " % (element, entry[element])
        return 0, ''

    def getVGInfo(self, args):
        vgUUID = args[0]
        info = self.s.getVGInfo(vgUUID)
        if info['status']['code']:
            return info['status']['code'], info['status']['message']
        #print info['info']
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
        dom = self.s.createVG(sdUUID, devList)
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

        con = dict(id="", connection=ip, port=port, iqn="", portal="",
            user=username, password=password)

        targets = self.s.discoverSendTargets(con)
        print "---- fullTargets"
        for target in targets['fullTargets']:
            print target
        print "---- targets"
        for target in targets['targets']:
            print target

        return 0, ''

    def getSessionList(self, args):
        sessions = self.s.getSessionList()
        for session in sessions['sessions']:
            print session

        return 0, ''

    def cleanupUnusedConnections(self, args):
        res = self.s.cleanupUnusedConnections()
        return res['status']['code'], res['status']['message']

    def connectStorageServer(self, args):
        serverType = int(args[0])
        spUUID = args[1]
        params = args[2].split(',')
        conList = []
        con = {}
        for item in params:
            key, value = item.split('=')
            con[key] = value
        conList.append(con)
        res = self.s.connectStorageServer(serverType, spUUID, conList)
        if res['status']['code']:
            return res['status']['code'], res['status']['message']
        return 0, ''

    def validateStorageServerConnection(self, args):
        serverType = int(args[0])
        spUUID = args[1]
        params = args[2].split(',')
        conList = []
        con = {}
        for item in params:
            key, value = item.split('=')
            con[key] = value
        conList.append(con)
        res = self.s.validateStorageServerConnection(serverType, spUUID, conList)
        if res['status']['code']:
            return res['status']['code'], res['status']['message']
        else:
            for i in res['statuslist']:
                print "Connection id %s - status %s" % (i['id'], i['status'])
        return 0, ''

    def disconnectStorageServer(self, args):
        serverType = int(args[0])
        spUUID = args[1]
        params = args[2].split(',')
        conList = []
        con = {}
        for item in params:
            key, value = item.split('=')
            con[key] = value
        conList.append(con)
        res = self.s.disconnectStorageServer(serverType, spUUID, conList)
        if res['status']['code']:
            return res['status']['code'], res['status']['message']
        return 0, ''

    def spmStart(self, args):
        validateArgTypes(args, [str, int, int, int, str, int, int])
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
        vmList = args[1]
        if len(args) >= 3:
            sdUUID = args[2]
        else:
            sdUUID = BLANK_UUID
        res = self.s.removeVM(spUUID, vmList, sdUUID)
        if res['status']['code']:
            return res['status']['code'], res['status']['message']
        return 0, ''

    def reconstructMaster(self, args):
        spUUID = args[0]
        poolName = args[1]
        masterDom = args[2]
        domList = args[3].split(",")
        domDict = {}
        for item in domList:
            key, value = item.split('=')
            domDict[key] = value
        mVer = int(args[4])
        if len(args) > 5:
            st = self.s.reconstructMaster(spUUID, poolName, masterDom, domDict,
                                          mVer, *args[5:])
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
            pool = self.s.createStoragePool(poolType, spUUID, poolName, masterDom, domList, mVer, *args[6:])
        else:
            pool = self.s.createStoragePool(poolType, spUUID, poolName, masterDom, domList, mVer)
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
        if len(args)>3:
            master = args[3]
        else:
            master = BLANK_UUID
        if len(args)>4:
            master_ver = int(args[4])
        else:
            master_ver = -1
        pool = self.s.connectStoragePool(spUUID, ID, scsi_key, master, master_ver)
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
        if len(args)>9:
            srcImgUUID = args[9]
        else:
            srcImgUUID = BLANK_UUID
        if len(args)>10:
            srcVolUUID = args[10]
        else:
            srcVolUUID = BLANK_UUID
        image = self.s.createVolume(sdUUID, spUUID, imgUUID, size, volFormat, preallocate,
                                    diskType, newVol, descr, srcImgUUID, srcVolUUID)
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
        uuid=args[3]
        info = self.s.getVolumePath(sdUUID, spUUID, imgUUID, uuid)
        if info['status']['code']:
            return info['status']['code'], info['status']['message']
        return 0, info['path']

    def getVolumeSize(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        uuid=args[3]
        size = self.s.getVolumeSize(sdUUID, spUUID, imgUUID, uuid)
        if size['status']['code']:
            return size['status']['code'], size['status']['message']
        del size['status']
        printDict(size)
        return 0, ''

    def extendVolume(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        volUUID = args[3]
        newSize = args[4]
        status = self.s.extendVolume(sdUUID, spUUID, imgUUID, volUUID, newSize)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        return 0, ''


    def uploadVolume(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        volUUID = args[3]
        srcPath = args[4]
        size = args[5]
        status = self.s.uploadVolume(sdUUID, spUUID, imgUUID, volUUID, srcPath, size, *args[6:])
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        return 0, ''


    def setVolumeDescription(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        volUUID = args[3]
        descr = args[4]
        status = self.s.setVolumeDescription(sdUUID, spUUID, imgUUID, volUUID, descr)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        return 0, ''

    def setVolumeLegality(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        imgUUID = args[2]
        volUUID = args[3]
        legality=args[4]
        image = self.s.setVolumeLegality(sdUUID, spUUID, imgUUID, volUUID, legality)
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
        status = self.s.deleteVolume(sdUUID, spUUID, imgUUID, volUUID, postZero, force)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        return 0, status['uuid']

    def deleteVolumeByDescr(self, args):
        sdUUID = args[1]
        spUUID = args[2]
        imgUUID = args[3]
        list = self.s.getVolumesList(sdUUID, spUUID, imgUUID)
        todelete = []
        if list['status']['code']:
            return list['status']['code'], list['status']['message']
        print "Images to delete:"
        for entry in list['uuidlist']:
            info = self.s.getVolumeInfo(sdUUID, spUUID, imgUUID, entry)['info']
            if info['description']:
                if args[0] in info['description']:
                    print "\t" + entry + " : " + info['description']
                    todelete.append(entry)
        if not len(todelete):
            return 0, 'Nothing to delete'
        var = raw_input("Are you sure yes/no?[no] :")
        if var=="yes":
            print self.s.deleteVolume(sdUUID, spUUID, imgUUID, todelete, 'false')
        return 0, ''

    def getVolumesList(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        if len(args)>2:
            images = [args[2]]
        else:
            list = self.s.getImagesList(sdUUID)
            if list['status']['code'] == 0:
                images = list['imageslist']

        for imgUUID in images:
            list = self.s.getVolumesList(sdUUID, spUUID, imgUUID)
            if list['status']['code']:
                return list['status']['code'], list['status']['message']

            for entry in list['uuidlist']:
                message = entry + ' : '
                res = self.s.getVolumeInfo(sdUUID, spUUID, imgUUID, entry)
                if not 'info' in res:
                    print 'ERROR:', entry, ':', res
                    continue
                info = res['info']
                if info['description']:
                    message += info['description'] + '. '
                if BLANK_UUID not in info['parent']:
                    message += 'Parent is ' + info['parent']
                print message
        return 0, ''

    def getFileList(self, args):
        validateArgTypes(args, [str, str])
        response = self.s.getFileList(*args)
        if response['status']['code']:
            return response['status']['code'], response['status']['message']

        for key, value in response['files'].iteritems():
            print 'file: ', key, 'status: ', value

        return 0, ''

    def getIsoList(self, args):
        spUUID = args[0]
        list = self.s.getIsoList(spUUID)
        if list['status']['code']:
            return list['status']['code'], list['status']['message']

        print '------ ISO list with proper permissions only -------'
        for entry in list['isolist']:
            print entry
        return 0, ''

    def getFloppyList(self, args):
        spUUID = args[0]
        list = self.s.getFloppyList(spUUID)
        if list['status']['code']:
            return list['status']['code'], list['status']['message']
        for entry in list['floppylist']:
            print entry
        return 0, ''

    def getImagesList(self, args):
        sdUUID = args[0]
        list = self.s.getImagesList(sdUUID)
        if list['status']['code']:
            return list['status']['code'], list['status']['message']
        for entry in list['imageslist']:
            print entry
        return 0, ''

    def getImageDomainsList(self, args):
        spUUID = args[0]
        imgUUID = args[1]
        if len(args) > 2:
            sdUUID = args[2]
            list = self.s.getImageDomainsList(spUUID, imgUUID, sdUUID)
        else:
            list = self.s.getImageDomainsList(spUUID, imgUUID)
        if list['status']['code']:
            return list['status']['code'], list['status']['message']
        for entry in list['domainslist']:
            print entry
        return 0, ''

    def getConnectedStoragePoolsList(self, args):
        list = self.s.getConnectedStoragePoolsList()
        if list['status']['code']:
            return list['status']['code'], list['status']['message']
        for entry in list['poollist']:
            print entry
        return 0, ''

    def clearAsyncTask(self, args):
        task = args[0]
        spUUID = args[1]
        status = self.s.clearAsyncTask(task, spUUID)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
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
        print "%s" % status # TODO

        return 0, ''

    def getAllTasksStatuses(self, args):
        status = self.s.getAllTasksStatuses()
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        print status # TODO
        return 0, ''

    def stopTask(self, args):
        taskID = args[0]
        status = self.s.stopTask(taskID)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        print status # TODO
        return 0, ''

    def clearTask(self, args):
        taskID = args[0]
        status = self.s.clearTask(taskID)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        print status # TODO
        return 0, ''

    def revertTask(self, args):
        taskID = args[0]
        status = self.s.revertTask(taskID)
        if status['status']['code']:
            return status['status']['code'], status['status']['message']
        print status # TODO
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
        image = self.s.moveImage(spUUID, srcDomUUID, dstDomUUID, imgUUID, vmUUID, op, postZero, force)
        if image['status']['code']:
            return image['status']['code'], image['status']['message']
        return 0, image['uuid']

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
        image = self.s.moveMultipleImages(spUUID, srcDomUUID, dstDomUUID, imgDict, vmUUID, force)
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
        image = self.s.copyImage(sdUUID, spUUID, vmUUID, srcImgUUID, srcVolUUID, dstImgUUID, dstVolUUID,
                                 descr, dstSdUUID, volType, volFormat, preallocate, postZero, force)
        if image['status']['code']:
            return image['status']['code'], image['status']['message']
        return 0, image['uuid']

    def mergeSnapshots(self, args):
        sdUUID = args[0]
        spUUID = args[1]
        vmUUID = args[2]
        imgUUID = args[3]
        ancestor=args[4]
        successor=args[5]
        if len(args) > 6:
            postZero = args[6]
        else:
            postZero = 'False'
        image = self.s.mergeSnapshots(sdUUID, spUUID, vmUUID, imgUUID, ancestor, successor, postZero)
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
        infos = self.s.getVmsInfo(spUUID, sdUUID,  vmList)
        if infos['status']['code'] != 0:
            return infos['status']['code'], infos['status']['message']
        else:
            message = ''
            for entry in infos['vmlist']:
                message += '\n' + '================================' + '\n'
                message += entry + '=' + str(infos['vmlist'][entry])
            if not message:
                message = 'No VMs found.'
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
            if len(kv) == 2:
                k, v = kv
            else:
                k, v = kv, ''
            d[k] = v
        return d

    def _parseDriveSpec(self, spec):
        if ',' in spec:
            d = {}
            for s in spec.split(','):
                k, v = s.split(':', 1)
                if k == 'domain': d['domainID'] = v
                if k == 'pool': d['poolID'] = v
                if k == 'image': d['imageID'] = v
                if k == 'volume': d['volumeID'] = v
                if k == 'boot': d['boot'] = v
                if k == 'format': d['format'] = v
            return d
        return spec

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
            if k in params: del params[k]
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
            if k in params: del params[k]
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
            if k in params: del params[k]
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

    def __image_status(self, imgUUID, list):
            if "imagestatus" in list and "message" in list:
                status = "OK"
                if list["imagestatus"]:
                    status = "ERROR"
                print "Image %s status %s: %s (%s)" % (imgUUID, status, list["message"], list["imagestatus"])
            if "badvols" in list:
                for v,err in list["badvols"].iteritems():
                    print "\tVolume %s is bad: %s" % (v, err)

    def __domain_status(self, sdUUID, list):
            if "domainstatus" in list and "message" in list:
                status = "OK"
                if list["domainstatus"]:
                    status = "ERROR"
                print "Domain %s status %s: %s (%s)" % (sdUUID, status, list["message"], list["domainstatus"])
            if "badimages" in list:
                for i in list["badimages"]:
                    print "\tImage %s is bad" % (i)
                    self.__image_status(i, list["badimages"][i])

    def __pool_status(self, spUUID, list):
            if "poolstatus" in list and "message" in list:
                status = "OK"
                if list["poolstatus"]:
                    status = "ERROR"
                print "Pool %s status %s: %s (%s)" % (spUUID, status, list["message"], list["poolstatus"])
            if "masterdomain":
                print "\tMaster domain is %s" % list["masterdomain"]
            if "spmhost":
                print "\tThe SPM host id is %s" % list["spmhost"]
            if "baddomains" in list:
                for d in list["baddomains"]:
                    print "\tDomain %s is bad:" % (d)
                    self.__domain_status(d, list["baddomains"][d])

    def checkImage(self, args):
        spUUID = args[0]
        sdUUID = args[1]
        images = args[2:]
        code = 0

        for imgUUID in images:
            list = self.s.checkImage(sdUUID, spUUID, imgUUID)
            if list['status']['code']:
                print "count not check image %s: code: %s message: %s" % (imgUUID, list['status']['code'], list['status']['message'])
                code = int(list['status']['code'])
                continue
            self.__image_status(imgUUID, list)
        return code, ''

    def checkDomain(self, args):
        spUUID = args[0]
        domains = args[1:]
        code = 0

        for sdUUID in domains:
            list = self.s.checkDomain(sdUUID, spUUID)
            if list['status']['code']:
                print "count not check domain %s: code: %s message: %s" % (sdUUID, list['status']['code'], list['status']['message'])
                code = int(list['status']['code'])
                continue
            self.__domain_status(sdUUID, list)
        return code, ''

    def checkPool(self, args):
        pools = args
        code = 0

        for spUUID in pools:
            list = self.s.checkPool(spUUID)
            if list['status']['code']:
                print "count not check pool %s: code: %s message: %s" % (spUUID, list['status']['code'], list['status']['message'])
                code = int(list['status']['code'])
                continue
            self.__pool_status(spUUID, list)
        return code, ''

    def repoStats(self, args):
        stats = self.s.repoStats()
        if stats['status']['code']:
            print "count not get repo stats"
            return int(list['status']['code'])
        for d in stats:
            if d == "status":
                continue
            print 'Domain %s %s' % (d, str(stats[d]))
        return 0, ''

if __name__ == '__main__':
    try:
        serv = service()
        opts, args = getopt.getopt(sys.argv[1:], "hms", ["help", "methods", "SSL", "truststore="])
        commands = {
            'create'  :  ( serv.do_create,
                           ('<configFile> [parameter=value, parameter=value, ......]',
                            'Creates new machine with the paremeters givven in the command line overriding the ones in the config file',
                            'Example with config file: vdsClient someServer create myVmConfigFile',
                            'Example with no file    : vdsClient someServer create /dev/null vmId=<uuid> memSize=256 imageFile=someImage display=<vnc|local|qxl|qxlnc>',
                            'Parameters list: r=required, o=optional',
                            'r   vmId=<uuid> : Unique identification for the created VM. Any additional operation on the VM must refer to this ID',
                            'o   vmType=<qemu/kvm> : Virtual machine technology - if not givven kvm is default',
                            'o   kvmEnable=<true/false> : run in KVM enabled mode or full emulation - default is according to the VDS capabilities',
                            'r   memSize=<int> : Memory to allocate for this machine',
                            'r   macAddr=<aa:bb:cc:dd:ee:ff> : MAC address of the machine',
                            'r   display=<vnc|local|qxl|qxlnc> : send the machine display to vnc, local host, spice, or spice with no image compression',
                            'o   drive=pool:poolID,domain:domainID,image:imageID,volume:volumeID[,boot:true,format:cow] : disk image by UUIDs',
                            'o   (deprecated) hda/b/c/d=<path> : Disk drive images',
                            'o   floppy=<image> : Mount the specified Image as floppy',
                            'o   cdrom=<path> : ISO image file to be mounted as the powerup cdrom',
                            'o   boot=<c/d/n> : boot device - drive C or cdrom or network',
                            'o   sysprepInf=/path/to/file: Launch with the specified file as sysprep.inf in floppy',
#                            'o   any parmeter=<any value> : parameter that is not familiar is passed as is to the VM',
#                            '                               and displayed with all other parameter. They can be used for additional',
#                            '                               information the user want to reserve with the machine'
                            'o   acpiEnable : If present will remove the default -no-acpi switch',
                            'o   tdf : If present will add the -rtc-td-hack switch',
                            'o   irqChip : If false, add the -no-kvm-irqchip switch',
                            'o   spiceSecureChannels : comma-separated list of spice channel that will be encrypted',
                            'o   spiceMonitors : number of emulated screen heads',
                            'o   soundDevice : emulated sound device',
                            'o   launchPaused : If "true", start qemu paused',
                            'o   vmName : human-readable name of new VM',
                            'o   tabletEnable : If "true", enable tablet input',
                            'o   timeOffset : guest\'s start date, relative to host\'s time, in seconds',
                            'o   smp : number of vcpus',
                            'o   smpCoresPerSocket, smpThreadsPerCore : vcpu topology',
                            'o   keyboardLayout : language code of client keyboard',
                            'o   cpuType : emulated cpu (with optional flags)',
                            'o   emulatedMachine : passed as qemu\'s -M',
                            )),
            'changeCD':  ( serv.do_changeCD,
                           ('<vmId> <fileName|drivespec>',
                            'Changes the iso image of the cdrom'
                           )),
            'changeFloppy':  ( serv.do_changeFloppy,
                           ('<vmId> <fileName|drivespec>',
                            'Changes the image of the floppy drive'
                           )),
            'destroy' :  ( serv.do_destroy,
                           ('<vmId>',
                            'Stops the emulation and destroys the virtual machine. This is not a shutdown.'
                           )),
            'shutdown' :  ( serv.do_shutdown,
                           ('<vmId> <timeout> <message>',
                            'Stops the emulation and graceful shutdown the virtual machine.'
                           )),
            'list'    :  ( serv.do_list,
                           ('[table]',
                            'Lists all available machines on the specified server and all available configuration info',
                            'If table modifier added then show table with the fields: vmId vmName Status IP'
                           )),
            'listNames'  :  ( serv.do_listNames,
                           ('Lists all available machines on the specified server',''
                           )),
            'pause'   :  ( serv.do_pause,
                           ('<vmId>',
                            'Pauses the execution of the virtual machine without termination'
                           )),
            'continue':  ( serv.do_continue,
                           ('<vmId>',
                            'Continues execution after of a paused machine'
                           )),
            'reset'   :  ( serv.do_reset,
                           ('<vmId>',
                            'Sends reset signal to the vm'
                           )),
            'setVmTicket': ( serv.do_setVmTicket,
                            ('<vmId> <password> <sec> [disconnect|keep|fail]',
                             'Set the password to the vm display for the next <sec> seconds.',
                             'Optional argument instructs spice regarding currently-connected client.'
                            )),
            'migrate':   ( serv.do_migrate,
                           ('vmId=<id> method=<offline|online> src=<host:[port]> dst=<host:[port]>',
                            'Migrate a desktop from src machine to dst host using the specified ports'
                           )),
            'migrateStatus': ( serv.do_mStat,
                              ('<vmId>',
                              'Check the progress of current outgoing migration'
                              )),
            'migrateCancel': ( serv.do_mCancel,
                               ('<vmId>',
                               '(not implemented) cancel machine migration'
                               )),
            'sendkeys':  ( serv.do_sendkeys,
                           ('<vmId> <key1> ...... <keyN>',
                            'Send the key sequence to the vm'
                           )),
            'getVdsCapabilities': ( serv.do_getCap,
                           ('',
                            'Get Capabilities info of the VDS'
                           )),
            'getVdsCaps': ( serv.do_getCap,
                           ('',
                            'Get Capabilities info of the VDS'
                           )),
            'getVdsStats': ( serv.do_getVdsStats,
                           ('',
                            'Get Statistics info on the VDS'
                           )),
            'getVmStats': ( serv.do_getVmStats,
                          ('<vmId>',
                            'Get Statistics info on the VM'
                           )),
            'getAllVmStats': ( serv.do_getAllVmStats,
                          ('',
                            'Get Statistics info for all existing VMs'
                           )),
            'getVGList' : ( serv.getVGList,
                           ('storageType',
                            'List of all VGs.'
                            )),
            'getDeviceList' : ( serv.getDeviceList,
                           ('[storageType]',
                            'List of all block devices (optionally - mathing storageType).'
                            )),
            'getDeviceInfo' : ( serv.getDeviceInfo,
                           ('<dev-guid>',
                            'Get block storage device info.'
                            )),
            'getDevicesVisibility' : ( serv.getDevicesVisibility,
                           ('<devlist>',
                            'Get visibility of each device listed'
                            )),
            'getVGInfo' : ( serv.getVGInfo,
                           ('<vgUUID>',
                            'Get info of VG'
                            )),
            'createVG' : ( serv.createVG,
                           ('<sdUUID> <devlist>',
                            'Create a new VG from devices devlist (list of dev GUIDs)'
                            )),
            'removeVG' : ( serv.removeVG,
                           ('<vgUUID>',
                            'remove the VG identified by its UUID'
                            )),
            'extendStorageDomain' : ( serv.extendStorageDomain,
                           ('<sdUUID> <spUUID> <devlist>',
                            'Extend the Storage Domain by adding devices devlist (list of dev GUIDs)'
                            )),
            'discoverST' : ( serv.discoverST,
                           ('ip[:port] [username password]',
                            'Discover the available iSCSI targetnames on a specified iSCSI portal'
                            )),
            'getSessionList' : ( serv.getSessionList,
                           ('',
                            'Collect the list of active SAN storage sessions'
                            )),
            'cleanupUnusedConnections' : ( serv.cleanupUnusedConnections,
                           ('',
                            'Clean up unused iSCSI storage connections'
                            )),
            'connectStorageServer' : ( serv.connectStorageServer,
                           ('<server type> <spUUID> <conList (id=...,connection=server:/export_path,portal=...,port=...,iqn=...,user=...,password=...[,initiatorName=...])>',
                            'Connect to a storage low level entity (server)'
                            )),
            'validateStorageServerConnection' : ( serv.validateStorageServerConnection,
                           ('<server type> <spUUID> <conList (id=...,connection=server:/export_path,portal=...,port=...,iqn=...,user=...,password=...[,initiatorName=...])>',
                            'Validate that we can connect to a storage server'
                            )),
            'disconnectStorageServer' : ( serv.disconnectStorageServer,
                           ('<server type> <spUUID> <conList (id=...,connection=server:/export_path,portal=...,port=...,iqn=...,user=...,password=...[,initiatorName=...])>',
                            'Disconnect from a storage low level entity (server)'
                            )),
            'spmStart' : ( serv.spmStart,
                           ('<spUUID> <prevID> <prevLVER> <recoveryMode> <scsiFencing> <maxHostID> <version>',
                            'Start SPM functionality'
                            )),
            'spmStop' : ( serv.spmStop,
                           ('<spUUID>',
                            'Stop SPM functionality'
                            )),
            'getSpmStatus' : ( serv.getSpmStatus,
                           ('<spUUID>',
                            'Get SPM status'
                            )),
            'acquireDomainLock' : ( serv.acquireDomainLock,
                           ('<spUUID> <sdUUID>',
                            'acquire storage domain lock'
                            )),
            'releaseDomainLock' : ( serv.releaseDomainLock,
                           ('<spUUID> <sdUUID>',
                            'release storage domain lock'
                            )),
            'fenceSpmStorage' : ( serv.fenceSpmStorage,
                           ('<spUUID> <prevID> <prevLVER> ',
                            'fence SPM storage state'
                            )),
            'updateVM' : ( serv.updateVM,
                           ("<spUUID> <vmList> ('vm'=vmUUID,'ovf'='...','imglist'='imgUUID1+imgUUID2+...') [sdUUID]",
                            'Update VM on pool or Backup domain'
                            )),
            'upgradeStoragePool' : ( serv.upgradeStoragePool,
                           ("<spUUID> <targetVersion>",
                            'Upgrade a pool to a new version (Requires a running SPM)'
                            )),
            'removeVM' : ( serv.removeVM,
                           ("<spUUID> <vmList> (vmUUID1,vmUUID2,...) [sdUUID]",
                            'Remove VM from pool or Backup domain'
                            )),
            'reconstructMaster' : ( serv.reconstructMaster,
                           ('<spUUID> <poolName> <masterDom> <domDict>({sdUUID1=status,sdUUID2=status,...}) <masterVersion>, [<lockPolicy> <lockRenewalIntervalSec> <leaseTimeSec> <ioOpTimeoutSec> <leaseRetries>]',
                            'Reconstruct master domain'
                            )),
            'createStoragePool' : ( serv.createStoragePool,
                           ('<storage type> <spUUID> <poolName> <masterDom> <domList>(sdUUID1,sdUUID2,...) <masterVersion>, [<lockPolicy> <lockRenewalIntervalSec> <leaseTimeSec> <ioOpTimeoutSec> <leaseRetries>]',
                            'Create new storage pool with single/multiple image data domain'
                            )),
            'destroyStoragePool' : ( serv.destroyStoragePool,
                           ('<spUUID> <id> <scsi-key>',
                            'Destroy storage pool'
                            )),
            'connectStoragePool' : ( serv.connectStoragePool,
                           ('<spUUID> <id> <scsi-key> [masterUUID] [masterVer]',
                            'Connect a Host to specific storage pool'
                            )),
            'disconnectStoragePool' : ( serv.disconnectStoragePool,
                           ('<spUUID> <id> <scsi-key>',
                            'Disconnect a Host from the specific storage pool'
                            )),
            'refreshStoragePool' : ( serv.refreshStoragePool,
                           ('<spUUID> <masterDom> <masterVersion>',
                            'Refresh storage pool'
                            )),
            'setStoragePoolDescription' : ( serv.setStoragePoolDescription,
                           ('<spUUID> <descr>',
                            'Set storage pool description'
                            )),
            'getStoragePoolInfo' : ( serv.getStoragePoolInfo,
                           ('<spUUID>',
                            'Get storage pool info'
                            )),
            'createStorageDomain' : ( serv.createStorageDomain,
                           ('<storage type> <domain UUID> <domain name> <param> <domType> <version>',
                            'Creates new storage domain'
                            )),
            'setStorageDomainDescription' : ( serv.setStorageDomainDescription,
                           ('<domain UUID> <descr>',
                            'Set storage domain description'
                            )),
            'validateStorageDomain' : ( serv.validateStorageDomain,
                           ('<domain UUID>',
                            'Validate storage domain'
                            )),
            'activateStorageDomain' : ( serv.activateStorageDomain,
                           ('<domain UUID> <pool UUID>',
                            'Activate a storage domain that is already a member in a storage pool.'
                            )),
            'deactivateStorageDomain' : ( serv.deactivateStorageDomain,
                           ('<domain UUID> <pool UUID> <new master domain UUID> <masterVer>',
                            'Deactivate a storage domain. '
                            )),
            'attachStorageDomain' : ( serv.attachStorageDomain,
                           ('<domain UUID> <pool UUID>',
                            'Attach a storage domain to a storage pool.'
                            )),
            'detachStorageDomain' : ( serv.detachStorageDomain,
                           ('<domain UUID> <pool UUID> <new master domain UUID> <masterVer>',
                            'Detach a storage domain from a storage pool.'
                            )),
            'forcedDetachStorageDomain' : ( serv.forcedDetachStorageDomain,
                           ('<domain UUID> <pool UUID>',
                            'Forced detach a storage domain from a storage pool.'
                            )),
            'formatStorageDomain' : ( serv.formatStorageDomain,
                           ('<domain UUID> [<autoDetach>]',
                            'Format detached storage domain.'
                            )),
            'getStorageDomainInfo' : ( serv.getStorageDomainInfo,
                           ('<domain UUID>',
                            'Get storage domain info.'
                            )),
            'getStorageDomainStats' : ( serv.getStorageDomainStats,
                           ('<domain UUID>',
                            'Get storage domain statistics.'
                            )),
            'getStorageDomainsList' : ( serv.getStorageDomainsList,
                           ('<pool UUID>',
                            'Get storage domains list of pool or all domains if pool omitted.'
                            )),
            'createVolume' : ( serv.createVolume,
                           ('<sdUUID> <spUUID> <imgUUID> <size> <volFormat> <preallocate> <diskType> <newVolUUID> <descr> <srcImgUUID> <srcVolUUID>',
                            'Creates new volume or snapshot'
                            )),
            'extendVolume' : ( serv.extendVolume,
                           ('<sdUUID> <spUUID> <imgUUID> <volUUID> <new disk size>',
                            'Extend volume (SAN only)'
                            )),
            'uploadVolume' : ( serv.uploadVolume,
                            ('<sdUUID> <spUUID> <imgUUID> <volUUID> <srcPath> <size>',
                            'Upload volume file into existing volume'
                            )),
            'getVolumePath' :  ( serv.getVolumePath,
                          ('<sdUUID> <spUUID> <imgUUID> <volume uuid>',
                           'Returns the path to the requested uuid'
                           )),
            'setVolumeDescription':   ( serv.setVolumeDescription,
                           ('<sdUUID> <spUUID> <imgUUID> <volUUID> <Description>',
                            'Sets a new description to the volume'
                            )),
            'setVolumeLegality':   ( serv.setVolumeLegality,
                          ('<sdUUID> <spUUID> <imgUUID> <volUUID> <Legality>',
                           'Set volume legality (ILLEGAL/LEGAL).'
                           )),
            'deleteVolume':( serv.deleteVolume,
                           ('<sdUUID> <spUUID> <imgUUID> <volUUID>,...,<volUUID> <postZero> [<force>]',
                            'Deletes an volume if its a leaf. Else returns error'
                            )),
            'deleteVolumeByDescr':( serv.deleteVolumeByDescr,
                           ('<part of description> <sdUUID> <spUUID> <imgUUID>',
                            'Deletes list of volumes(only leafs) according to their description'
                            )),
            'getVolumeInfo': ( serv.getVolumeInfo,
                          ('<sdUUID> <spUUID> <imgUUID> <volUUID>',
                           'Returns all the volume details'
                           )),
            'getParent'  : ( serv.getParent,
                             ('<sdUUID> <spUUID> <imgUUID> <Disk Image uuid>',
                             'Returns the parent of the volume. Error if no parent exists'
                            )),
            'getVolumesList': ( serv.getVolumesList,
                             ('<sdUUID> <spUUID> [imgUUID]',
                              'Returns list of volumes of imgUUID or sdUUID if imgUUID absent'
                             )),
            'getVolumeSize': ( serv.getVolumeSize,
                             ('<sdUUID> <spUUID> <imgUUID> <volUUID>',
                              'Returns the apparent size and the true size of the volume (in bytes)'
                             )),
            'getFileList': ( serv.getFileList,
                             ('<sdUUID> [pattern]',
                              'Returns files list from ISO domain'
                             )),
            'getIsoList': ( serv.getIsoList,
                             ('<spUUID>',
                              'Returns list of all .iso images in ISO domain'
                             )),
            'getFloppyList': ( serv.getFloppyList,
                             ('<spUUID>',
                              'Returns list of all .vfd images in ISO domain'
                             )),
            'getImagesList': ( serv.getImagesList,
                             ('<sdUUID>',
                              'Get list of all images of specific domain'
                             )),
            'getImageDomainsList': ( serv.getImageDomainsList,
                             ('<spUUID> <imgUUID> [datadomain=True]',
                              'Get list of all data domains in the pool that contains imgUUID'
                             )),
            'getConnectedStoragePoolsList': ( serv.getConnectedStoragePoolsList,
                             ('',
                              'Get storage pools list'
                             )),
            'clearAsyncTask': ( serv.clearAsyncTask,
                             ('<Task name> <spUUID>',
                             'Clear asynchronous task'
                             )),
            'getTaskInfo': ( serv.getTaskInfo,
                             ('<TaskID>',
                             'get async task info'
                             )),
            'getAllTasksInfo': ( serv.getAllTasksInfo,
                             ('',
                             'get info of all async tasks'
                             )),
            'getTaskStatus': ( serv.getTaskStatus,
                             ('<TaskID>',
                             'get task status'
                             )),
            'getAllTasksStatuses': ( serv.getAllTasksStatuses,
                             ('',
                             'list statuses of all async tasks'
                             )),
            'stopTask': ( serv.stopTask,
                             ('<TaskID>',
                             'stop async task'
                             )),
            'clearTask': ( serv.clearTask,
                             ('<TaskID>',
                             'clear async task'
                             )),
            'revertTask': ( serv.revertTask,
                             ('<TaskID>',
                             'revert async task'
                             )),
            'prepareForShutdown': ( serv.prepareForShutdown,
                             ('',''
                             )),
            'setLogLevel': ( serv.do_setLogLevel,
                             ('<level> [logName][,logName]...', 'set log verbosity level (10=DEBUG, 50=CRITICAL'
                             )),
            'deleteImage': ( serv.deleteImage,
                           ('<sdUUID> <spUUID> <imgUUID> [<postZero>] [<force>]',
                           'Delete Image folder with all volumes.',
                           )),
            'moveImage': ( serv.moveImage,
                           ('<spUUID> <srcDomUUID> <dstDomUUID> <imgUUID> <vmUUID> <op = COPY_OP/MOVE_OP> [<postZero>] [ <force>]',
                           'Move/Copy image between storage domains within same storage pool'
                           )),
            'moveMultiImage': ( serv.moveMultiImage,
                           ('<spUUID> <srcDomUUID> <dstDomUUID> <imgList>({imgUUID=postzero,imgUUID=postzero,...}) <vmUUID> [<force>]',
                           'Move multiple images between storage domains within same storage pool'
                           )),
            'copyImage': ( serv.copyImage,
                           ('<sdUUID> <spUUID> <vmUUID> <srcImgUUID> <srcVolUUID> <dstImgUUID> <dstVolUUID> <dstDescr> <dstSdUUID> <volType> <volFormat> <preallocate> [<postZero>] [<force>]',
                            'Create new template/volume from VM.',
                            'Do it by collapse and copy the whole chain (baseVolUUID->srcVolUUID)'
                           )),
            'mergeSnapshots': ( serv.mergeSnapshots,
                          ('<sdUUID> <spUUID> <vmUUID> <imgUUID> <Ancestor Image uuid> <Successor Image uuid> [<postZero>]',
                           'Merge images from successor to ancestor.',
                           'The result is a image named as successor image and contents the data of whole successor->ancestor chain'
                           )),
            'desktopLogin': ( serv.desktopLogin,
                          ('<vmId> <domain> <user> <password>',
                           'Login to vmId desktop using the supplied credentials'
                           )),
            'desktopLogoff':( serv.desktopLogoff,
                           ('<vmId> <force>',
                             'Lock user session. force should be set to true/false'
                           )),
            'desktopLock': ( serv.desktopLock,
                           ('<vmId>',
                             'Logoff current user'
                            )),
            'sendHcCmd': ( serv.sendHcCmd,
                           ('<vmId> <message>',
                             'Sends a message to a specific VM through Hypercall channel'
                            )),
            'hibernate': ( serv.hibernate,
                           ('<vmId>',
                             'Hibernates the desktop'
                            )),
            'monitorCommand': ( serv.monitorCommand,
                           ('<vmId> <string>',
                             'Send a string containing monitor command to the desktop'
                            )),
            'getVmsInfo': ( serv.do_getVmsInfo,
                          ('<Import path> <Import type> <VM type>',
                           'Return list of import candidates with their info'
                           )),
            'getVmsList': ( serv.do_getVmsList,
                          ('<spUUID> [sdUUID]',
                           'Get list of VMs from the pool or domain if sdUUID given. Run only from the SPM.'
                           )),
            'addNetwork':   ( serv.do_addNetwork,
                           ('bridge=<bridge> [vlan=<number>] [bond=<bond>] nics=nic[,nic]',
                            'Add a new network to this vds.'
                           )),
            'delNetwork':   ( serv.do_delNetwork,
                           ('bridge=<bridge> [vlan=<number>] [bond=<bond>] nics=nic[,nic]',
                            'Remove a network (and parts thereof) from this vds.'
                           )),
            'editNetwork':   ( serv.do_editNetwork,
                           ('oldBridge=<bridge> newBridge=<bridge> [vlan=<number>] [bond=<bond>] nics=nic[,nic]',
                            'Replace a network with a new one.'
                           )),
            'setSafeNetworkConfig':   ( serv.do_setSafeNetworkConfig,
                           ('',
                            'declare current network configuration as "safe"'
                           )),
            'fenceNode':   ( serv.do_fenceNode,
                           ('<addr> <port> <agent> <user> <passwd> <action> [<secure> [<options>]] \n\t<action> is one of (status, on, off, reboot),\n\t<agent> is one of (rsa, ilo, ipmilan, drac5, etc)\n\t<secure> (true|false) may be passed to some agents',
                            'send a fencing command to a remote node'
                           )),
            'checkImage':  ( serv.checkImage,
                           ('<spUUID> <sdUUID> <imgUUID>...',
                            "check Image(s)"
                           )),
            'checkDomain':  ( serv.checkDomain,
                           ('<spUUID><sdUUID>...',
                            "check Image(s)"
                           )),
            'checkPool':  ( serv.checkPool,
                           ('<spUUID>...',
                            "check Image(s)"
                           )),
            'repoStats':  ( serv.repoStats,
                           ('',
                           "Get the the health status of the active domains"
                          )),
        }

        for o,v in opts:
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
        if len(args) < 2:
            raise Exception("Need at least two arguments")
        server, command = args[0:2]
        if command not in commands:
            raise Exception("Unknown command")
        server, serverPort = vdscli.cannonizeAddrPort(server).split(':', 1)

    except SystemExit, status:
        sys.exit(status)
    except Exception, e:
        print "ERROR - %s"%(e)
        usage(commands)
        sys.exit(-1)


    try:
        serv.do_connect(server, serverPort)
        try:
            commandArgs = args[2:]
        except:
            commandArgs = []
        code, message = commands[command][0](commandArgs)
        if code != 0: code = 1
        print message
        sys.exit(code)
    except (TypeError, IndexError, ValueError), e:
        print "Error using command:", e, "\n"
        print command
        for line in commands[command][1]:
            print '\t' + line
        sys.exit(-1)
    except SystemExit, status:
        sys.exit(status)
    except socket.error, e:
        if e[0] == 111:
            print "Connection to %s:%s refused" % (server, serverPort)
        else:
            traceback.print_exc()
        sys.exit(-1)
    except:
        traceback.print_exc()
        sys.exit(-1)
