#
# Copyright 2012 Red Hat, Inc.
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

import os
import tempfile
import pwd
import grp
import fnmatch
import shutil
import logging
from contextlib import contextmanager
from functools import partial, wraps

from testrunner import VdsmTestCase as TestCaseBase
from testrunner import permutations, expandPermutations
from nose.plugins.skip import SkipTest
try:
    import rtslib
except ImportError:
    pass

from vdsm.config import config
from vdsm.constants import VDSM_USER, VDSM_GROUP, QEMU_PROCESS_USER, EXT_SUDO
import storage.sd
import storage.storage_exception as se
import storage.volume
from storage.misc import execCmd
from storage.misc import RollbackContext
from vdsm.utils import CommandPath
from vdsm import vdscli

_VARTMP = '/var/tmp'

if not config.getboolean('vars', 'xmlrpc_enable'):
    raise SkipTest("XML-RPC Bindings are disabled")

_mkinitrd = CommandPath("mkinird", "/usr/bin/mkinitrd")
_modprobe = CommandPath("modprobe", "/usr/sbin/modprobe")
_exportfs = CommandPath("exportfs", "/usr/sbin/exportfs")


def readableBy(filePath, user):
    rc, out, err = execCmd([EXT_SUDO, '-u', user, 'head', '-c', '0', filePath])
    return rc == 0


@contextmanager
def kernelBootImages():
    kernelVer = os.uname()[2]
    kernelPath = "/boot/vmlinuz-" + kernelVer
    initramfsPath = "/boot/initramfs-%s.img" % kernelVer

    if not os.path.isfile(kernelPath):
        raise SkipTest("Can not locate kernel image for release %s" %
                       kernelVer)
    if not readableBy(kernelPath, QEMU_PROCESS_USER):
        raise SkipTest("qemu process can not read the file %s" % kernelPath)

    if os.path.isfile(initramfsPath):
        # There is an initramfs shipped with the distro, try use it
        if not readableBy(initramfsPath, QEMU_PROCESS_USER):
            raise SkipTest("qemu process can not read the file %s" %
                           initramfsPath)
        try:
            yield (kernelPath, initramfsPath)
        finally:
            pass
    else:
        # Generate an initramfs on demand, use it, delete it
        initramfsPath = genInitramfs(kernelVer)
        try:
            yield (kernelPath, initramfsPath)
        finally:
            os.unlink(initramfsPath)


def genInitramfs(kernelVer):
    fd, path = tempfile.mkstemp()
    cmd = [_mkinitrd.cmd, "-f", path, kernelVer]
    rc, out, err = execCmd(cmd, sudo=False)
    os.chmod(path, 0644)
    return path


def skipNoKVM(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        r = self.s.getVdsCapabilities()
        self.assertVdsOK(r)
        if r['info']['kvmEnabled'] != 'true':
            raise SkipTest('KVM is not enabled')
        return method(self, *args, **kwargs)
    return wrapped


@expandPermutations
class XMLRPCTest(TestCaseBase):
    UPSTATES = frozenset(('Up', 'Powering up', 'Running'))

    def setUp(self):
        isSSL = config.getboolean('vars', 'ssl')
        if isSSL and os.geteuid() != 0:
            raise SkipTest("Must be root to use SSL connection to server")
        self.s = vdscli.connect(useSSL=isSSL)

    def testGetCaps(self):
        r = self.s.getVdsCapabilities()
        self.assertVdsOK(r)

    def assertVmUp(self, vmid):
        r = self.s.getVmStats(vmid)
        self.assertVdsOK(r)
        self.myAssertIn(r['statsList'][0]['status'], self.UPSTATES)

    def assertGuestUp(self, vmid):
        r = self.s.getVmStats(vmid)
        self.assertVdsOK(r)
        self.assertEquals(r['statsList'][0]['status'], 'Up')

    def myAssertIn(self, member, container, msg=None):
        "Poor man's reimplementation of Python2.7's unittest.assertIn"

        if hasattr(self, 'assertIn'):
            return self.assertIn(member, container, msg)

        if msg is None:
            msg = '%r not found in %r' % (member, container)

        self.assertTrue(member in container, msg)

    def assertVdsOK(self, vdsResult):
        # code == 0 means OK
        self.assertEquals(
            vdsResult['status']['code'], 0,
            'error code: %s, message: %s' % (vdsResult['status']['code'],
                                             vdsResult['status']['message']))

    @skipNoKVM
    def testStartEmptyVM(self):
        VMID = '66666666-ffff-4444-bbbb-333333333333'

        r = self.s.create({'memSize': '100', 'display': 'vnc', 'vmId': VMID,
                           'vmName': 'foo'})
        self.assertVdsOK(r)
        try:
            self.retryAssert(lambda: self.assertVmUp(VMID), timeout=20)
        finally:
            # FIXME: if the server dies now, we end up with a leaked VM.
            r = self.s.destroy(VMID)
            self.assertVdsOK(r)

    @skipNoKVM
    def testStartSmallVM(self):
        pciAddress = {'slot': '0x03', 'bus': '0x00', 'domain': '0x0000',
                      'function': '0x0', 'type': 'pci'}
        interfaceDev = {'nicModel': 'virtio', 'macAddr': '52:54:00:59:F5:3F',
                        'network': '', 'address': pciAddress,
                        'device': 'bridge', 'type': 'interface',
                        'linkActive': True, 'filter': 'no-mac-spoofing'}
        customization = {'vmId': '77777777-ffff-3333-bbbb-222222222222',
                         'vmName': 'vdsm_testSmallVM',
                         'devices': [interfaceDev]}

        self._runVMKernelBootTemplate(customization)

    def _runVMKernelBootTemplate(self, vmDef={}, distro='fedora'):
        kernelArgsDistro = {
            # Fedora: The initramfs is generated by dracut. The following
            # arguments will be interpreted by init scripts created by dracut.
            'fedora': 'rd.break=cmdline rd.shell rd.skipfsck'}
        kernelArgsDistro['rhel'] = kernelArgsDistro['fedora']
        if distro.lower() not in kernelArgsDistro:
            raise SkipTest("Don't know how to perform direct kernel boot for "
                           "%s" % distro)

        template = {'vmId': '11111111-abcd-2222-ffff-333333333333',
                    'vmName': 'vdsmKernelBootVM',
                    'display': 'vnc',
                    'kvmEnable': 'true',
                    'memSize': '256',
                    'vmType': 'kvm',
                    'kernelArgs': kernelArgsDistro[distro]}
        template.update(vmDef)
        vmid = template['vmId']

        def assertVMAndGuestUp():
            self.assertVmUp(vmid)
            self.assertGuestUp(vmid)

        with kernelBootImages() as (kernelPath, initramfsPath):
            template.update(
                {'kernel': kernelPath,
                 'initrd': initramfsPath})
            try:
                self.assertVdsOK(self.s.create(template))
                # wait 65 seconds for VM to come up until timeout
                self.retryAssert(assertVMAndGuestUp, timeout=65)
            finally:
                destroyResult = self.s.destroy(vmid)

        self.assertVdsOK(destroyResult)

    @permutations([['localfs'], ['iscsi'], ['glusterfs'], ['nfs']])
    def testStorage(self, backendType):
        conf = storageLayouts[backendType]
        with RollbackContext() as rollback:
            self._createVdsmStorageLayout(conf, rollback)

    def _generateDriveConf(self, conf):
        drives = []
        for poolid, domains in conf['layout'].iteritems():
            for sdid, imageList in domains.iteritems():
                for imgid in imageList:
                    volume = conf['img'][imgid]
                    drives.append({'poolID': poolid,
                                   'domainID': sdid,
                                   'imageID': imgid,
                                   'volumeID': volume['volid'],
                                   'format': volume['format']})
        return drives

    @skipNoKVM
    @permutations([['localfs'], ['iscsi'], ['nfs']])
    def testSimpleVMWithStorage(self, backendType):
        conf = storageLayouts[backendType]
        drives = self._generateDriveConf(conf)
        customization = {'vmId': '88888888-eeee-ffff-aaaa-111111111111',
                         'vmName': 'vdsm_testSmallVM_' + backendType,
                         'drives': drives}

        with RollbackContext() as rollback:
            self._createVdsmStorageLayout(conf, rollback)
            self._runVMKernelBootTemplate(customization)

    def _createVdsmStorageLayout(self, conf, rollback):
        backendServer = conf['server'](self.s, self)
        connDef = conf['conn']
        storageDomains = conf['sd']
        storagePools = conf['sp']
        images = conf['img']
        layout = conf['layout']

        typeSpecificArgs = backendServer.prepare(connDef, rollback)
        self._createStorageDomain(storageDomains, typeSpecificArgs, rollback)
        self._detachExistingStoragePool(rollback)
        self._createStoragePool(storagePools, rollback)
        self._startSPM(storagePools, rollback)
        self._attachStorageDomain(storagePools, layout, rollback)
        self._createVolume(images, layout, rollback)

    def _createStorageDomain(self, storageDomains, typeSpecificArgs, rollback):
        for sdid, domain in storageDomains.iteritems():
            specificArg = typeSpecificArgs[domain['connUUID']]

            # clean up possible leftovers in the previous test run
            r = self.s.getStorageDomainInfo(sdid)
            if r['status']['code'] in [0, se.StorageDomainAccessError.code]:
                self.assertVdsOK(self.s.formatStorageDomain(sdid, True))
            else:
                self.assertEquals(
                    r['status']['code'], se.StorageDomainDoesNotExist.code)

            r = self.s.createStorageDomain(
                storage.sd.name2type(domain['type']), sdid, domain['name'],
                specificArg, storage.sd.name2class(domain['class']), 0)
            self.assertVdsOK(r)
            undo = lambda sdid=sdid: \
                self.assertVdsOK(self.s.formatStorageDomain(sdid, True))
            rollback.prependDefer(undo)

    def _detachExistingStoragePool(self, rollback):
        r = self.s.getConnectedStoragePoolsList()
        self.assertVdsOK(r)
        exPools = r['poollist']
        for poolid in exPools:
            r = self.s.getSpmStatus(poolid)
            self.assertVdsOK(r)
            spmStatus = r['spm_st']
            if spmStatus['spmStatus'] == 'SPM':
                r = self.s.spmStop(poolid)
                self.assertVdsOK(r)
            self.s.disconnectStoragePool(poolid, 1, 'scsikey')

    def _createStoragePool(self, storagePools, rollback):
        # For now we actually just support 1 pool
        # So there must be only 1 pool definition in the configuration
        # This code is written to create pools in case we support several pools
        poolType = 0  # not used
        for poolid, pool in storagePools.iteritems():
            r = self.s.createStoragePool(
                poolType, poolid, pool['name'], pool['master_uuid'],
                [pool['master_uuid']], pool['master_ver'])
            self.assertVdsOK(r)
            r = self.s.connectStoragePool(
                poolid, pool['host'], 'scsikey', pool['master_uuid'],
                pool['master_ver'])
            self.assertVdsOK(r)

    def _startSPM(self, storagePools, rollback):
        # If spmstart fails, there is no good rollback because we need to
        # be spm to tear down the pool
        for poolid in storagePools.keys():
            r = self.s.spmStart(poolid, -1, -1, -1, 0)
            self.assertVdsOK(r)
            tid = r['uuid']
            self._waitTask(tid)
            undo = lambda poolid=poolid: \
                self.assertVdsOK(self.s.destroyStoragePool(
                    poolid, storagePools[poolid]['host'], 'scsiKey'))
            rollback.prependDefer(undo)

    def _attachStorageDomain(self, storagePools, layout, rollback):
        for poolid, domains in layout.iteritems():
            for sdid in domains.keys():
                # Master domain is already active, skip
                if sdid != storagePools[poolid]['master_uuid']:
                    r = self.s.attachStorageDomain(sdid, poolid)
                    self.assertVdsOK(r)
                    undo = lambda sdid=sdid, poolid=poolid: \
                        self.assertVdsOK(
                            self.s.detachStorageDomain(
                                sdid, poolid, storage.sd.BLANK_UUID,
                                storagePools[poolid]['master_ver']))
                    rollback.prependDefer(undo)
                    r = self.s.activateStorageDomain(sdid, poolid)
                    self.assertVdsOK(r)

    def _createVolume(self, images, layout, rollback):
        for poolid, domains in layout.iteritems():
            for sdid, imageList in domains.iteritems():
                for imgid in imageList:
                    volume = images[imgid]
                    r = self.s.createVolume(
                        sdid, poolid, imgid, volume['size'],
                        storage.volume.name2type(volume['format']),
                        storage.volume.name2type(volume['preallocate']),
                        storage.volume.name2type(volume['type']),
                        volume['volid'], volume['description'])
                    self.assertVdsOK(r)
                    tid = r['uuid']
                    self._waitTask(tid)
                    undo = lambda sdid=sdid, poolid=poolid, \
                        imgid=imgid, volid=volume['volid']: \
                        self._waitTask(
                            self.s.deleteVolume(
                                sdid, poolid, imgid, [volid])['uuid'])
                    rollback.prependDefer(undo)

    def _waitTask(self, taskId):
        def assertTaskOK():
            r = self.s.getTaskStatus(taskId)
            self.assertVdsOK(r)
            state = r['taskStatus']['taskState']
            self.assertEquals(state, 'finished')

        self.retryAssert(assertTaskOK, timeout=20)


class BackendServer(object):
    '''Super class of various backend servers'''

    def __init__(self, vdsmServer, asserts):
        self.s = vdsmServer
        self.asserts = asserts

    def _createBackend(self, backends, rollback):
        raise RuntimeError("Not implemented")

    def _assertBackendConnected(self, connections):
        r = self.s.storageServer_ConnectionRefs_statuses()
        self.asserts.assertVdsOK(r)
        status = r['connectionslist']
        for refid in connections:
            self.asserts.assertEquals(status[refid]['connected'], True)

    def _connectBackend(self, connections, timeout, rollback):
        r = self.s.storageServer_ConnectionRefs_acquire(connections)
        self.asserts.assertVdsOK(r)
        undo = lambda: self.asserts.assertVdsOK(
            self.s.storageServer_ConnectionRefs_release(connections.keys()))
        rollback.prependDefer(undo)
        for _refid, status in r['results'].iteritems():
            self.asserts.assertEquals(status, 0)
        self.asserts.retryAssert(
            partial(self._assertBackendConnected, connections),
            timeout=timeout)

    def _genTypeSpecificArgs(self, connections, rollback):
        raise RuntimeError("Not implemented")

    def prepare(self, backendDef, rollback):
        connections = self._createBackend(backendDef['backends'], rollback)
        self._connectBackend(
            connections, backendDef['timeout'], rollback)
        return self._genTypeSpecificArgs(connections, rollback)


class LocalFSServer(BackendServer):
    def _createBackend(self, backends, rollback):
        uid = pwd.getpwnam(VDSM_USER)[2]
        gid = grp.getgrnam(VDSM_GROUP)[2]

        rootDir = tempfile.mkdtemp(prefix='localfs', dir=_VARTMP)
        undo = lambda: os.rmdir(rootDir)
        rollback.prependDefer(undo)
        os.chown(rootDir, uid, gid)
        os.chmod(rootDir, 0755)

        connections = {}
        for uuid, subDir in backends.iteritems():
            path = os.path.join(rootDir, subDir)
            os.mkdir(path)
            undo = lambda path=path: shutil.rmtree(path, ignore_errors=True)
            rollback.prependDefer(undo)
            os.chown(path, uid, gid)
            os.chmod(path, 0775)

            connections[uuid] = {'type': 'localfs',
                                 'params': {'path': path}}

        return connections

    def _genTypeSpecificArgs(self, connections, rollback):
        args = {}
        for uuid, conn in connections.iteritems():
            args[uuid] = conn['params']['path']
        return args


class IscsiServer(BackendServer):
    def __init__(self, vdsmServer, asserts):
        # check if the system supports configuring iSCSI target
        if not "rtslib" in globals().keys():
            raise SkipTest("python-rtslib is not installed.")

        cmd = [_modprobe.cmd, "iscsi_target_mod"]
        rc, out, err = execCmd(cmd, sudo=True)
        asserts.assertEquals(rc, 0)

        super(IscsiServer, self).__init__(vdsmServer, asserts)
        self.address = '127.0.0.1'

    def _createTarget(self, iqn, imgPath, rollback):
        '''Using LIO Python binding to configure iSCSI target.

        LIO can export various types of backend storage object as LUN, and
        support many fabric modules like iSCSI, FCoE.

        The backstores/fileio/image hierachy and iscsi/target/tpg/lun
        hierarchy are managed separately. Their lifecycles are independent.
        Create the backstore hierachy and the iSCSI hierachy, then attach the
        image file to the lun.

        For more infomation, see http://www.linux-iscsi.org/wiki/ISCSI .
        '''
        fio = rtslib.FileIOStorageObject(
            os.path.basename(imgPath), imgPath, os.path.getsize(imgPath))
        rollback.prependDefer(fio.delete)

        iscsiMod = rtslib.FabricModule('iscsi')
        tgt = rtslib.Target(iscsiMod, iqn, mode='create')
        # Target.delete() will delete all
        # TPGs, ACLs, LUNs, Portals recursively
        rollback.prependDefer(tgt.delete)
        # TPG is a group of network portals
        tpg = rtslib.TPG(tgt, None, mode='create')
        rtslib.LUN(tpg, None, fio)
        # Enable demo mode, grant all initiators to access all LUNs in the TPG
        tpg.set_attribute('generate_node_acls', '1')
        tpg.set_attribute('cache_dynamic_acls', '1')
        # Do not use any authentication methods
        tpg.set_attribute('authentication', '0')
        # Allow writing to LUNs in demo mode
        tpg.set_attribute('demo_mode_write_protect', '0')
        # Activate the TPG otherwise it is not able to access the LUNs in it
        tpg.enable = True
        # Bind to '127.0.0.1' so it's OK to use demo mode just for testing
        rtslib.NetworkPortal(tpg, self.address, 3260, mode='create')

    def _createBackend(self, backends, rollback):
        connections = {}
        self.vgNames = {}
        for uuid, conn in backends.iteritems():
            fd, imgPath = tempfile.mkstemp(dir=_VARTMP)
            rollback.prependDefer(partial(os.unlink, imgPath))
            rollback.prependDefer(partial(os.close, fd))
            # Create a 10GB empty disk image
            os.ftruncate(fd, 1024 ** 3 * 10)
            iqn = conn['iqn']
            self._createTarget(iqn, imgPath, rollback)
            connections[uuid] = {
                'type': 'iscsi',
                'params': {'portal': {'host': self.address}, 'iqn': iqn}}
            self.vgNames[uuid] = conn['vgName']

        return connections

    def _createVG(self, vgName, devName, rollback):
        r = self.s.createVG(vgName, [devName])
        self.asserts.assertVdsOK(r)
        vgid = r['uuid']
        rollback.prependDefer(
            lambda: self.asserts.assertVdsOK(
                self.s.removeVG(vgid)))
        return vgid

    def _getIqnDevs(self, iqns):
        '''find the related devices under iqns'''
        r = self.s.getDeviceList()
        devList = r['devList']
        self.asserts.assertVdsOK(r)
        iqnDevs = {}
        for iqn in iqns:
            for dev in devList:
                if iqn in map(lambda p: p['iqn'], dev['pathlist']):
                    iqnDevs[iqn] = dev['GUID']
                    break
            else:
                raise AssertionError(
                    'Can not find related device of iqn %s' % iqn)
        return iqnDevs

    def _genTypeSpecificArgs(self, connections, rollback):
        iqns = [conn['params']['iqn'] for conn in connections.itervalues()]
        # If two iSCSI tests are run back to back, it takes VDSM some time to
        # refresh the iSCSI session info.
        iqnDevs = self.asserts.retryAssert(partial(self._getIqnDevs, iqns),
                                           timeout=30)

        args = {}
        for uuid, conn in connections.iteritems():
            iqn = conn['params']['iqn']
            vgid = self._createVG(self.vgNames[uuid], iqnDevs[iqn], rollback)
            args[uuid] = vgid

        return args


class GlusterFSServer(BackendServer):
    def __init__(self, vdsmServer, asserts):
        super(GlusterFSServer, self).__init__(vdsmServer, asserts)

        # Check if gluster service is operational
        self.glusterVolInfo = self.s.glusterVolumesList()
        if self.glusterVolInfo['status']['code'] != 0:
            raise SkipTest(self.glusterVolInfo['status']['message'])

    def _createBackend(self, backendDef, rollback):
        connections = {}
        for uuid, conDict in backendDef.iteritems():
            spec = conDict['spec']
            vfstype = conDict['vfstype']
            options = conDict['options']

            # Check if gluster volume is created & started
            glusterVolName = spec.split(':')[1]
            if not glusterVolName in self.glusterVolInfo['volumes']:
                raise SkipTest("Test volume %s not found. Pls create it "
                               "and start it" % glusterVolName)

            glusterVolDict = self.glusterVolInfo['volumes'][glusterVolName]
            if glusterVolDict['volumeStatus'] == 'OFFLINE':
                raise SkipTest("Test volume %s is offline. \
                                Pls start the volume" % glusterVolName)

            connections[uuid] = {'type': 'glusterfs',
                                 'params': {'spec': spec,
                                            'vfsType': vfstype,
                                            'options': options}}

        return connections

    def _genTypeSpecificArgs(self, connections, rollback):
        args = {}
        for uuid, conDict in connections.iteritems():
            args[uuid] = conDict['params']['spec']
        return args


def exportNFS(path):
    rc, out, err = execCmd([_exportfs.cmd, '-o', 'rw,insecure,sync',
                            '127.0.0.1:%s' % path])
    return rc


def unexportNFS(path):
    rc, out, err = execCmd([_exportfs.cmd, '-u', '127.0.0.1:%s' % path])
    return rc


def listNFS():
    rc, out, err = execCmd([_exportfs.cmd])
    if rc != 0:
        raise RuntimeError("Can not list NFS export: %s\n" % err)
    return out


def cleanNFSLeftovers(pathPrefix):
    pathPattern = pathPrefix + "*"
    exports = listNFS()
    for line in exports:
        export = line.split(" ", 1)[0]
        if fnmatch.fnmatch(export, pathPattern):
            if unexportNFS(export) == 0:
                shutil.rmtree(export, ignore_errors=True)
            else:
                logging.warning("Failed to unexport NFS entry %s", export)


class NFSServer(BackendServer):
    def _createBackend(self, backends, rollback):
        prefix = 'vdsmFunctionalTestNfs'

        cleanNFSLeftovers(os.path.join(_VARTMP, prefix))

        uid = pwd.getpwnam(VDSM_USER)[2]
        gid = grp.getgrnam(VDSM_GROUP)[2]

        rootDir = tempfile.mkdtemp(prefix=prefix, dir=_VARTMP)
        undo = lambda: os.rmdir(rootDir)
        rollback.prependDefer(undo)
        os.chown(rootDir, uid, gid)
        os.chmod(rootDir, 0755)

        connections = {}
        for uuid, subDir in backends.iteritems():
            path = os.path.join(rootDir, subDir)
            os.mkdir(path)
            undo = lambda path=path: shutil.rmtree(path, ignore_errors=True)
            rollback.prependDefer(undo)
            os.chown(path, uid, gid)
            os.chmod(path, 0775)
            self.asserts.assertEquals(0, exportNFS(path))
            undo = lambda path=path: self.asserts.assertEquals(
                0, unexportNFS(path))
            rollback.prependDefer(undo)

            connections[uuid] = {'type': 'nfs',
                                 'params': {'export': '127.0.0.1:%s' % path}}

        return connections

    def _genTypeSpecificArgs(self, connections, rollback):
        args = {}
        for uuid, conn in connections.iteritems():
            args[uuid] = conn['params']['export']
        return args


storageLayouts = \
    {'localfs':
        {'server': LocalFSServer,
         'conn': {
             'backends': {
                 '53acd629-47e6-42d8-ba99-cd0b12ff0e1e': 'teststorage0',
                 '87e618fe-587c-4704-a9f8-9fd9321fd907': 'teststorage1'},
             'timeout': 30},
         'sd': {
             "def32ac7-1234-1234-8a8c-1c887333fe65": {
                 "name": "test domain0", "type": "localfs", "class": "Data",
                 "connUUID": "53acd629-47e6-42d8-ba99-cd0b12ff0e1e"},
             "9af9bd7f-6167-4ae8-aac6-95a5e5f36f60": {
                 "name": "test domain1", "type": "localfs", "class": "Data",
                 "connUUID": "87e618fe-587c-4704-a9f8-9fd9321fd907"}},
         'sp': {
             "6e4d6a96-1234-1234-8905-b5eec55c1535": {
                 "name": "local storage pool", "master_ver": 1, "host": 1,
                 "master_uuid": "def32ac7-1234-1234-8a8c-1c887333fe65"}},
         'img': {
             "47bd7538-c48b-4b94-ba94-def922151d48": {
                 "description": "Test volume0", "type": "leaf",
                 "volid": "11bd7538-c48b-4b94-ba94-def922151d48",
                 "format": "cow", "preallocate": "sparse", "size": 20971520},
             "bace8f68-4c5a-43f2-acb4-fa8daf58c0f9": {
                 "description": "test volume1", "type": "leaf",
                 "volid": "bb3cbda6-a711-45a6-a6f2-c32661939e93",
                 "format": "cow", "preallocate": "sparse", "size": 20971520}},
         'layout': {
             # pool
             "6e4d6a96-1234-1234-8905-b5eec55c1535": {
                 # domains
                 "def32ac7-1234-1234-8a8c-1c887333fe65": [
                     # images
                     "47bd7538-c48b-4b94-ba94-def922151d48"],
                 "9af9bd7f-6167-4ae8-aac6-95a5e5f36f60": [
                     "bace8f68-4c5a-43f2-acb4-fa8daf58c0f9"]}}},
     'nfs':
        {'server': NFSServer,
         'conn': {
             'backends': {
                 '7663ae6f-045e-4bfa-b3cf-7ab738ee42c9': 'nfs0',
                 '402b9d69-d3f7-4855-87c3-95257ffc8c6a': 'nfs1'},
             'timeout': 30},
         'sd': {
             "c29e3337-27c2-4fd6-8caa-9404e0455769": {
                 "name": "test nfs domain0", "type": "nfs", "class": "Data",
                 "connUUID": "7663ae6f-045e-4bfa-b3cf-7ab738ee42c9"},
             "78e5e27e-833c-4977-b940-58b4f83599ac": {
                 "name": "test nfs domain1", "type": "nfs", "class": "Data",
                 "connUUID": "402b9d69-d3f7-4855-87c3-95257ffc8c6a"}},
         'sp': {
             "01da0617-2da4-4081-8ad0-60b4e18d26bb": {
                 "name": "nfs storage pool", "master_ver": 1, "host": 1,
                 "master_uuid": "c29e3337-27c2-4fd6-8caa-9404e0455769"}},
         'img': {
             "ca31643e-699b-4268-86d0-fd377bf85f3b": {
                 "description": "Test nfs volume0", "type": "leaf",
                 "volid": "b74f92d5-4846-4918-91ed-2028677a628c",
                 "format": "cow", "preallocate": "sparse", "size": 20971520},
             "a913f26d-c880-4c0b-bc21-2901b6ba912a": {
                 "description": "test nfs volume1", "type": "leaf",
                 "volid": "15a87231-5bab-41d3-8c74-b9f7bc1d8c46",
                 "format": "cow", "preallocate": "sparse", "size": 20971520}},
         'layout': {
             "01da0617-2da4-4081-8ad0-60b4e18d26bb": {
                 "c29e3337-27c2-4fd6-8caa-9404e0455769": [
                     "ca31643e-699b-4268-86d0-fd377bf85f3b"],
                 "78e5e27e-833c-4977-b940-58b4f83599ac": [
                     "a913f26d-c880-4c0b-bc21-2901b6ba912a"]}}},
     'iscsi': {
         'server': IscsiServer,
         'conn': {
             'backends': {
                 '3bd3092e-096b-4409-a2de-e10313a0d8af': {
                     'iqn': 'iqn.2012-12.org.ovirt.tests:vdsmtests0',
                     'vgName': '3f330c2c-9b01-4167-9df5-cf665f95e3a6'},
                 '28ba1368-9f5c-4441-a7fd-94e85435564b': {
                     'iqn': 'iqn.2012-12.org.ovirt.tests:vdsmtests1',
                     'vgName': 'a73a818b-3341-457a-8139-a6a71194ab7a'}},
             'timeout': 50},
         'sd': {
             "3f330c2c-9b01-4167-9df5-cf665f95e3a6": {
                 "name": "test iscsi domain0",
                 "type": "iscsi", "class": "Data",
                 "connUUID": "3bd3092e-096b-4409-a2de-e10313a0d8af"},
             "a73a818b-3341-457a-8139-a6a71194ab7a": {
                 "name": "test iscsi domain1",
                 "type": "iscsi", "class": "Data",
                 "connUUID": "28ba1368-9f5c-4441-a7fd-94e85435564b"}},
         'sp': {
             "39178935-1f2e-4cd1-8c2d-4f47097d80a3": {
                 "name": "iscsi storage pool", "master_ver": 1, "host": 1,
                 "master_uuid": "3f330c2c-9b01-4167-9df5-cf665f95e3a6"}},
         'img': {
             "a81db3fc-5586-4e35-9785-912c28ada09d": {
                 "description": "Test iscsi volume0", "type": "leaf",
                 "volid": "a921cdf0-b322-4ee8-84e6-8e87c65c016f",
                 "format": "cow", "preallocate": "sparse", "size": 20971520},
             "35c728e1-edf1-4068-8f25-02d21feb85cd": {
                 "description": "test iscsi volume1", "type": "leaf",
                 "volid": "eb42c709-42a2-4227-a5b6-f368df3a2613",
                 "format": "cow", "preallocate": "sparse", "size": 20971520}},
         'layout': {
             "39178935-1f2e-4cd1-8c2d-4f47097d80a3": {
                 "3f330c2c-9b01-4167-9df5-cf665f95e3a6": [
                     "a81db3fc-5586-4e35-9785-912c28ada09d"],
                 "a73a818b-3341-457a-8139-a6a71194ab7a": [
                     "35c728e1-edf1-4068-8f25-02d21feb85cd"]}}},
     'glusterfs':
        {'server': GlusterFSServer,
         'conn': {
             'backends': {
                 '98a4b463-8e38-4b54-814e-dbf5b5fdf437': {
                     'spec': 'localhost:testvol', 'vfstype': 'glusterfs',
                     'options': ""}},
             'timeout': 30},
         'sd': {
             '3cca273c-9fe5-4740-aad4-26a94ca13716': {
                 'name': 'test gluster domain', 'type': 'glusterfs',
                 'class': 'Data',
                 'connUUID': '98a4b463-8e38-4b54-814e-dbf5b5fdf437'}},
         'sp': {
             '8a098016-9495-4c1b-95da-6ca3238c0cbd': {
                 'name': 'test gluster storage pool', 'master_ver': 1,
                 'host': 1,
                 'master_uuid': '3cca273c-9fe5-4740-aad4-26a94ca13716'}},
         'img': {
             '72214f6c-f8c0-41fc-8123-df6d0d6e934d': {
                 'description': 'test gluster volume', 'type': 'leaf',
                 'volid': 'c0ca016f-c1a5-4d66-9428-03f7a99c16bc',
                 'format': 'cow', 'preallocate': 'sparse',
                 'size': 20971520}},
         'layout': {
             # pool
             '8a098016-9495-4c1b-95da-6ca3238c0cbd': {
                 # domain(s)
                 '3cca273c-9fe5-4740-aad4-26a94ca13716': [
                     # images(s)
                     '72214f6c-f8c0-41fc-8123-df6d0d6e934d']}}}}
