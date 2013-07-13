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
from pwd import getpwnam
import platform
import sys
import os
import stat
import errno
from functools import wraps
import threading
import re
import getopt
import signal
import logging
import logging.config

LOG_CONF_PATH = "/etc/vdsm/svdsm.logger.conf"

try:
    logging.config.fileConfig(LOG_CONF_PATH)
except:
    logging.basicConfig(filename='/dev/stdout', filemode='w+',
                        level=logging.DEBUG)
    log = logging.getLogger("SuperVdsm.Server")
    log.warn("Could not init proper logging", exc_info=True)

from storage import fuser
from multiprocessing import Pipe, Process
from gluster import listPublicFunctions
import storage.misc as misc
from vdsm import utils
from parted_utils import getDevicePartedInfo as _getDevicePartedInfo
from md_utils import getMdDeviceUuidMap as _getMdDeviceUuidMap

from lsblk import getLsBlk as _getLsBlk
from storage.multipath import getScsiSerial as _getScsiSerial
from storage.iscsi import forceScsiScan as _forceScsiScan
from storage.iscsi import getDevIscsiInfo as _getdeviSCSIinfo
from storage.iscsi import readSessionInfo as _readSessionInfo
from supervdsm import _SuperVdsmManager
from storage.fileUtils import chown, resolveGid, resolveUid
from storage.fileUtils import validateAccess as _validateAccess
from vdsm.constants import METADATA_GROUP, EXT_UDEVADM, \
    DISKIMAGE_USER, DISKIMAGE_GROUP, P_LIBVIRT_VMCHANNELS, VDSM_USER
from storage.devicemapper import _removeMapping, _getPathsStatus
import configNetwork
from vdsm.config import config
import tc
import ksm
import mkimage
from storage.multipath import MPATH_CONF
import sourceRouteThread

_UDEV_RULE_FILE_DIR = "/etc/udev/rules.d/"
_UDEV_RULE_FILE_PREFIX = "99-vdsm-"
_UDEV_RULE_FILE_EXT = ".rules"
_UDEV_RULE_FILE_NAME = _UDEV_RULE_FILE_DIR + _UDEV_RULE_FILE_PREFIX + \
    "%s-%s" + _UDEV_RULE_FILE_EXT

RUN_AS_TIMEOUT = config.getint("irs", "process_pool_timeout")


class Timeout(RuntimeError):
    pass


def logDecorator(func):
    callbackLogger = logging.getLogger("SuperVdsm.ServerCallback")

    @wraps(func)
    def wrapper(*args, **kwargs):
        callbackLogger.debug('call %s with %s %s',
                             func.__name__, args[1:], kwargs)
        try:
            res = func(*args, **kwargs)
        except:
            callbackLogger.error("Error in %s", func.__name__, exc_info=True)
            raise
        callbackLogger.debug('return %s with %s',
                             func.__name__, res)
        return res
    return wrapper

KB = 2 ** 10
TEST_BUFF_LEN = 4 * KB


class _SuperVdsm(object):

    UDEV_WITH_RELOAD_VERSION = 181

    log = logging.getLogger("SuperVdsm.ServerCallback")

    @logDecorator
    def ping(self, *args, **kwargs):
        # This method exists for testing purposes
        return True

    @logDecorator
    def getHardwareInfo(self, *args, **kwargs):
        if platform.machine() in ('x86_64', 'i686'):
            from dmidecodeUtil import getHardwareInfoStructure
            return getHardwareInfoStructure()
        else:
            #  not implemented over other architecture
            return {}

    @logDecorator
    def getDevicePartedInfo(self, *args, **kwargs):
        return _getDevicePartedInfo(*args, **kwargs)

    @logDecorator
    def getMdDeviceUuidMap(self, *args, **kwargs):
        return _getMdDeviceUuidMap(*args, **kwargs)

    @logDecorator
    def getLsBlk(self, *args, **kwargs):
        return _getLsBlk(*args, **kwargs)

    @logDecorator
    def readMultipathConf(self):
        with open(MPATH_CONF) as f:
            return [x.strip("\n") for x in f.readlines()]

    @logDecorator
    def getScsiSerial(self, *args, **kwargs):
        return _getScsiSerial(*args, **kwargs)

    @logDecorator
    def forceScsiScan(self, *args, **kwargs):
        return _forceScsiScan(*args, **kwargs)

    @logDecorator
    def removeDeviceMapping(self, devName):
        return _removeMapping(devName)

    @logDecorator
    def getdeviSCSIinfo(self, *args, **kwargs):
        return _getdeviSCSIinfo(*args, **kwargs)

    @logDecorator
    def readSessionInfo(self, sessionID):
        return _readSessionInfo(sessionID)

    @logDecorator
    def getPathsStatus(self):
        return _getPathsStatus()

    @logDecorator
    def getVmPid(self, vmName):
        pidFile = "/var/run/libvirt/qemu/%s.pid" % vmName
        return open(pidFile).read()

    @logDecorator
    def prepareVmChannel(self, socketFile):
        if socketFile.startswith(P_LIBVIRT_VMCHANNELS):
            mode = os.stat(socketFile).st_mode | stat.S_IWGRP
            os.chmod(socketFile, mode)
        else:
            raise Exception("Incorporate socketFile")

    @logDecorator
    def addNetwork(self, bridge, options):
        return configNetwork.addNetwork(bridge, **options)

    @logDecorator
    def delNetwork(self, bridge, options):
        return configNetwork.delNetwork(bridge, **options)

    @logDecorator
    def editNetwork(self, oldBridge, newBridge, options):
        return configNetwork.editNetwork(oldBridge, newBridge, **options)

    @logDecorator
    def setupNetworks(self, networks, bondings, options):
        return configNetwork.setupNetworks(networks, bondings, **options)

    def _runAs(self, user, groups, func, args=(), kwargs={}):
        def child(pipe):
            res = ex = None
            try:
                uid = resolveUid(user)
                if groups:
                    gids = map(resolveGid, groups)

                    os.setgid(gids[0])
                    os.setgroups(gids)
                os.setuid(uid)

                res = func(*args, **kwargs)
            except BaseException as e:
                ex = e

            pipe.send((res, ex))
            pipe.recv()

        pipe, hisPipe = Pipe()
        proc = Process(target=child, args=(hisPipe,))
        proc.start()

        if not pipe.poll(RUN_AS_TIMEOUT):
            try:
                os.kill(proc.pid, signal.SIGKILL)
            except OSError as e:
                # If it didn't fail because process is already dead
                if e.errno != errno.ESRCH:
                    raise

            raise Timeout()

        res, err = pipe.recv()
        pipe.send("Bye")
        proc.terminate()
        if err is not None:
            raise err

        return res

    @logDecorator
    def validateAccess(self, user, groups, *args, **kwargs):
        return self._runAs(user, groups, _validateAccess, args=args,
                           kwargs=kwargs)

    @logDecorator
    def setSafeNetworkConfig(self):
        return configNetwork.setSafeNetworkConfig()

    @logDecorator
    def udevTrigger(self, guid):
        self.__udevReloadRules(guid)
        cmd = [EXT_UDEVADM, 'trigger', '--verbose', '--action', 'change',
               '--property-match=DM_NAME=%s' % guid]
        rc, out, err = misc.execCmd(cmd, sudo=False)
        if rc:
            raise OSError(errno.EINVAL, "Could not trigger change for device \
                          %s, out %s\nerr %s" % (guid, out, err))

    @logDecorator
    def appropriateDevice(self, guid, thiefId):
        ruleFile = _UDEV_RULE_FILE_NAME % (guid, thiefId)
        rule = 'SYMLINK=="mapper/%s", OWNER="%s", GROUP="%s"\n' % (guid,
               DISKIMAGE_USER, DISKIMAGE_GROUP)
        with open(ruleFile, "w") as rf:
            rf.write(rule)

    @logDecorator
    def rmAppropriateRules(self, thiefId):
        re_apprDevRule = "^" + _UDEV_RULE_FILE_PREFIX + ".*?-" + thiefId + \
                         _UDEV_RULE_FILE_EXT + "$"
        rules = [os.path.join(_UDEV_RULE_FILE_DIR, r) for r in
                 os.listdir(_UDEV_RULE_FILE_DIR)
                 if re.match(re_apprDevRule, r)]
        fails = []
        for r in rules:
            try:
                os.remove(r)
            except OSError:
                fails.append(r)
        return fails

    @logDecorator
    def ksmTune(self, tuningParams):
        '''
        Set KSM tuning parameters for MOM, which runs without root privilege
        when it's lauched by vdsm. So it needs supervdsm's assistance to tune
        KSM's parameters.
        '''
        return ksm.tune(tuningParams)

    @logDecorator
    def setPortMirroring(self, networkName, ifaceName):
        '''
        Copy networkName traffic of a bridge to an interface

        :param networkName: networkName bridge name to capture the traffic from
        :type networkName: string

        :param ifaceName: ifaceName to copy (mirror) the traffic to
        :type ifaceName: string

        this commands mirror all 'networkName' traffic to 'ifaceName'
        '''
        tc.setPortMirroring(networkName, ifaceName)

    @logDecorator
    def unsetPortMirroring(self, networkName, target):
        '''
        Release captured mirror networkName traffic from networkName bridge

        :param networkName: networkName to release the traffic capture
        :type networkName: string
        :param target: target device to release
        :type target: string
        '''
        tc.unsetPortMirroring(networkName, target)

    @logDecorator
    def mkFloppyFs(self, vmId, files, volId):
        return mkimage.mkFloppyFs(vmId, files, volId)

    @logDecorator
    def mkIsoFs(self, vmId, files, volId):
        return mkimage.mkIsoFs(vmId, files, volId)

    @logDecorator
    def removeFs(self, path):
        return mkimage.removeFs(path)

    @logDecorator
    def fuser(self, *args):
        return fuser.fuser(*args)

    def __udevReloadRules(self, guid):
        if self.__udevOperationReload():
            reload = "--reload"
        else:
            reload = "--reload-rules"
        cmd = [EXT_UDEVADM, 'control', reload]
        rc, out, err = misc.execCmd(cmd, sudo=False)
        if rc:
            self.log.error("Udevadm reload-rules command failed rc=%s, "
                           "out=\"%s\", err=\"%s\"", rc, out, err)
            raise OSError(errno.EINVAL, "Could not reload-rules for device "
                          "%s" % guid)

    @utils.memoized
    def __udevVersion(self):
        cmd = [EXT_UDEVADM, '--version']
        rc, out, err = misc.execCmd(cmd, sudo=False)
        if rc:
            self.log.error("Udevadm version command failed rc=%s, "
                           " out=\"%s\", err=\"%s\"", rc, out, err)
            raise RuntimeError("Could not get udev version number")
        return int(out[0])

    def __udevOperationReload(self):
        return self.__udevVersion() > self.UDEV_WITH_RELOAD_VERSION


def main(sockfile, pidfile=None):
    log = logging.getLogger("SuperVdsm.Server")

    def bind(func):
        def wrapper(_SuperVdsm, *args, **kwargs):
            return func(*args, **kwargs)
        return wrapper

    for name, func in listPublicFunctions():
        setattr(_SuperVdsm, name, logDecorator(bind(func)))

    try:
        log.debug("Making sure I'm root - SuperVdsm")
        if os.geteuid() != 0:
            sys.exit(errno.EPERM)

        if pidfile:
            pid = str(os.getpid())
            with open(pidfile, 'w') as f:
                f.write(pid + "\n")

        log.debug("Parsing cmd args")
        address = sockfile

        log.debug("Cleaning old socket %s", address)
        if os.path.exists(address):
            os.unlink(address)

        log.debug("Setting up keep alive thread")

        try:
            log.debug("Creating remote object manager")
            manager = _SuperVdsmManager(address=address, authkey='')
            manager.register('instance', callable=_SuperVdsm)

            server = manager.get_server()
            servThread = threading.Thread(target=server.serve_forever)
            servThread.setDaemon(True)
            servThread.start()

            chown(address, getpwnam(VDSM_USER).pw_uid, METADATA_GROUP)

            log.debug("Started serving super vdsm object")

            threading.Thread(target=sourceRouteThread.subscribeToInotifyLoop(),
                             name='sourceRoute').start()

            # Python bug of thread.join() will block signal
            # http://bugs.python.org/issue1167930
            while servThread.isAlive():
                servThread.join(5)
        finally:
            if os.path.exists(address):
                utils.rmFile(address)

    except Exception:
        log.error("Could not start Super Vdsm", exc_info=True)
        sys.exit(1)


def _usage():
    print "Usage:  supervdsmServer --sockfile=fullPath [--pidfile=fullPath]"


def _parse_args():
    argDict = {}
    opts, args = getopt.getopt(sys.argv[1:], "h", ["sockfile=", "pidfile="])
    for o, v in opts:
        o = o.lower()
        if o == "--sockfile":
            argDict['sockfile'] = v
        elif o == "--pidfile":
            argDict['pidfile'] = v
        else:
            _usage()
            sys.exit(1)
    if 'sockfile' not in argDict:
        _usage()
        sys.exit(1)

    return argDict


if __name__ == '__main__':
    argDict = _parse_args()
    main(**argDict)
