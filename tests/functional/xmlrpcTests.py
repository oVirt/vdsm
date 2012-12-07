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
import shutil
from contextlib import contextmanager

from testrunner import VdsmTestCase as TestCaseBase
from nose.plugins.skip import SkipTest

from vdsm.config import config
from vdsm.constants import VDSM_USER, VDSM_GROUP, QEMU_PROCESS_USER, EXT_SUDO
import storage.sd
import storage.volume
from storage.misc import execCmd
from storage.misc import RollbackContext
from vdsm.utils import CommandPath
from vdsm import vdscli

if not config.getboolean('vars', 'xmlrpc_enable'):
    raise SkipTest("XML-RPC Bindings are disabled")

_mkinitrd = CommandPath("mkinird", "/usr/bin/mkinitrd")


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
    def wrapped(self):
        r = self.s.getVdsCapabilities()
        self.assertVdsOK(r)
        if r['info']['kvmEnabled'] != 'true':
            raise SkipTest('KVM is not enabled')
        return method(self)
    wrapped.func_name = method.func_name
    return wrapped


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
        customization = {'vmId': '77777777-ffff-3333-bbbb-222222222222',
                         'vmName': 'vdsm_testSmallVM'}

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

    def testLocalfs(self):
        conf = storageLayouts['localfs']
        with RollbackContext() as rollback:
            self._createVdsmStorageLayout(conf, rollback)

    @skipNoKVM
    def testSimpleVMoLocalfs(self):
        localfs = storageLayouts['localfs']
        drives = []
        for poolid, domains in localfs['layout'].iteritems():
            for sdid, imageList in domains.iteritems():
                for imgid in imageList:
                    volume = localfs['img'][imgid]
                    drives.append({'poolID': poolid,
                                   'domainID': sdid,
                                   'imageID': imgid,
                                   'volumeID': volume['volid'],
                                   'format': volume['format']})
        customization = {'vmId': '88888888-eeee-ffff-aaaa-111111111111',
                         'vmName': 'vdsm_testSmallVM_localfs',
                         'drives': drives}

        with RollbackContext() as rollback:
            self._createVdsmStorageLayout(localfs, rollback)
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
        self._createImage(images, layout, rollback)

    def _createStorageDomain(self, storageDomains, typeSpecificArgs, rollback):
        for sdid, domain in storageDomains.iteritems():
            specificArg = typeSpecificArgs[domain['connUUID']]
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

    def _createImage(self, images, layout, rollback):
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
                    undo = lambda sdid=sdid, poolid=poolid, imgid=imgid: \
                        self._waitTask(
                            self.s.deleteImage(
                                sdid, poolid, imgid)['uuid'])
                    rollback.prependDefer(undo)

    def _waitTask(self, taskId):
        def assertTaskOK():
            r = self.s.getTaskStatus(taskId)
            self.assertVdsOK(r)
            state = r['taskStatus']['taskState']
            self.assertEquals(state, 'finished')

        self.retryAssert(assertTaskOK, timeout=20)


class LocalFSServer(object):
    def __init__(self, vdsmServer, asserts):
        self.s = vdsmServer
        self.asserts = asserts

    def _createBackend(self, backendDef, rollback):
        uid = pwd.getpwnam(VDSM_USER)[2]
        gid = grp.getgrnam(VDSM_GROUP)[2]

        rootDir = tempfile.mkdtemp(prefix='localfs')
        undo = lambda: os.rmdir(rootDir)
        rollback.prependDefer(undo)
        os.chown(rootDir, uid, gid)
        os.chmod(rootDir, 0755)

        connections = {}
        for uuid, subDir in backendDef.iteritems():
            path = os.path.join(rootDir, subDir)
            os.mkdir(path)
            undo = lambda path=path: shutil.rmtree(path, ignore_errors=True)
            rollback.prependDefer(undo)
            os.chown(path, uid, gid)
            os.chmod(path, 0775)

            connections[uuid] = {'type': 'localfs',
                                 'params': {'path': path}}

        return connections

    def _connectBackend(self, connections, rollback):
        r = self.s.storageServer_ConnectionRefs_acquire(connections)
        self.asserts.assertVdsOK(r)
        undo = lambda: self.asserts.assertVdsOK(
            self.s.storageServer_ConnectionRefs_release(connections.keys()))
        rollback.prependDefer(undo)
        for _refid, status in r['results'].iteritems():
            self.asserts.assertEquals(status, 0)

    def _genTypeSpecificArgs(self, connections, rollback):
        args = {}
        for uuid, conn in connections.iteritems():
            args[uuid] = conn['params']['path']
        return args

    def prepare(self, backendDef, rollback):
        connections = self._createBackend(backendDef, rollback)
        self._connectBackend(connections, rollback)
        return self._genTypeSpecificArgs(connections, rollback)


class IscsiServer(object):
    def __init__(self, vdsmServer):
        self.s = vdsmServer

    def _createBackend(self, backendDef, rollback):
        # Create iscsi target
        pass

    def _connectBackend(self, connections, rollback):
        # Connect iscsi storage server
        pass

    def _genTypeSpecificArgs(self, connections, rollback):
        # Create VG
        # Generate UUIDs of those VG
        pass

    def prepare(self, backendDef, rollback):
        pass


storageLayouts = \
    {'localfs':
        {'server': LocalFSServer,
         'conn': {'53acd629-47e6-42d8-ba99-cd0b12ff0e1e': 'teststorage0',
                  '87e618fe-587c-4704-a9f8-9fd9321fd907': 'teststorage1'},
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
     'nfs': {'server': 'blah', 'conn': 'blah', 'sd': 'blah', 'sp': 'blah',
             'img': 'blah', 'layout': 'blah'},
     'iscsi': {'server': 'blah', 'conn': 'blah', 'sd': 'blah', 'sp': 'blah',
               'img': 'blah', 'layout': 'blah'}}
