# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import binascii
import logging
import os
import stat
import shutil
import tempfile
import threading

import six

from contextlib import contextmanager

from testlib import make_file, make_uuid
from testlib import maybefail
from testlib import recorded

from storage.storagefakelib import (
    FakeLVM,
    FakeStorageDomainCache,
)

from . import qemuio

from monkeypatch import MonkeyPatchScope

from vdsm import utils
from vdsm.common.units import KiB, MiB
from vdsm.storage import blockSD
from vdsm.storage import blockVolume
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import fileSD
from vdsm.storage import fileUtils
from vdsm.storage import fileVolume
from vdsm.storage import guarded
from vdsm.storage import hsm
from vdsm.storage import nbd
from vdsm.storage import outOfProcess as oop
from vdsm.storage import qemuimg
from vdsm.storage import sd
from vdsm.storage import volume

NR_PVS = 2        # The number of fake PVs we use to make a fake VG by default
WAIT_TIMEOUT = 5  # Used for Callable event default wait timeout

log = logging.getLogger("test")


@contextmanager
def temp_dir(base="/var/tmp", path=None):
    """
    If path is specified, use given path instead of a temporary directory.
    Needed when the tests must use the same directory as another program
    running during the tests. An example use case is running supervdsm with
    --data-center option.
    """
    if path is None:
        path = tempfile.mkdtemp(dir=base)
    else:
        # Fail if the directory exists, since we are going to delete it at the
        # end of the test.
        os.makedirs(path)
    try:
        yield path
    finally:
        shutil.rmtree(path)


class FakeFileEnv(object):
    def __init__(self, tmpdir, sd_manifest, sdcache):
        self.tmpdir = tmpdir
        self.sd_manifest = sd_manifest
        self.sdcache = sdcache

    def make_volume(self, size, imguuid, voluuid, parent_vol_id=sc.BLANK_UUID,
                    vol_format=sc.RAW_FORMAT, vol_type=sc.LEAF_VOL,
                    prealloc=sc.SPARSE_VOL, disk_type=sc.DATA_DISKTYPE,
                    desc='fake volume', qcow2_compat='0.10', legal=True):
        return make_file_volume(self.sd_manifest, size, imguuid, voluuid,
                                parent_vol_id, vol_format, vol_type,
                                prealloc, disk_type, desc, qcow2_compat,
                                legal)


class FakeBlockEnv(object):
    def __init__(self, tmpdir, sd_manifest, sdcache, lvm):
        self.tmpdir = tmpdir
        self.sd_manifest = sd_manifest
        self.sdcache = sdcache
        self.lvm = lvm

    def make_volume(self, size, imguuid, voluuid, parent_vol_id=sc.BLANK_UUID,
                    vol_format=sc.RAW_FORMAT, vol_type=sc.LEAF_VOL,
                    prealloc=sc.SPARSE_VOL, disk_type=sc.DATA_DISKTYPE,
                    desc='fake volume', qcow2_compat='0.10'):
        return make_block_volume(self.lvm, self.sd_manifest, size, imguuid,
                                 voluuid, parent_vol_id, vol_format,
                                 vol_type, prealloc, disk_type, desc,
                                 qcow2_compat)


@contextmanager
def fake_file_env(obj=None, sd_version=3, data_center=None,
                  remote_path="server:/path"):
    with temp_dir(path=data_center) as tmpdir:
        mnt_dir = os.path.join(tmpdir, "mnt")
        local_path = fileUtils.transformPath(remote_path)
        mountpoint = os.path.join(mnt_dir, local_path)
        os.makedirs(mountpoint)

        fake_sdc = FakeStorageDomainCache()
        with MonkeyPatchScope([
            [sc, 'REPO_DATA_CENTER', tmpdir],
            [sc, 'REPO_MOUNT_DIR', mnt_dir],
            [volume, 'sdCache', fake_sdc],
            [fileVolume, 'sdCache', fake_sdc],
            [hsm, 'sdCache', fake_sdc],
            [nbd, 'sdCache', fake_sdc],
        ]):
            sd_manifest = make_filesd_manifest(
                mountpoint, sd_version=sd_version)
            fake_sdc.domains[sd_manifest.sdUUID] = FakeSD(sd_manifest)
            try:
                yield FakeFileEnv(tmpdir, sd_manifest, fake_sdc)
            finally:
                oop.stop()


@contextmanager
def fake_block_env(obj=None, sd_version=3, data_center=None):
    with temp_dir(path=data_center) as tmpdir:
        lvm = FakeLVM(tmpdir)
        fake_sdc = FakeStorageDomainCache()
        with MonkeyPatchScope([
            (blockSD, 'lvm', lvm),
            (blockVolume, 'lvm', lvm),
            (blockVolume, 'sdCache', fake_sdc),
            (sc, 'REPO_DATA_CENTER', tmpdir),
            (sc, "REPO_MOUNT_DIR", os.path.join(tmpdir, sc.DOMAIN_MNT_POINT,
                                                sd.BLOCKSD_DIR)),
            (volume, 'sdCache', fake_sdc),
            (hsm, 'sdCache', fake_sdc),
            [nbd, 'sdCache', fake_sdc],
        ]):
            sd_manifest = make_blocksd_manifest(tmpdir, lvm,
                                                sd_version=sd_version)
            fake_sdc.domains[sd_manifest.sdUUID] = FakeSD(sd_manifest, lvm)
            try:
                yield FakeBlockEnv(tmpdir, sd_manifest, fake_sdc, lvm)
            finally:
                oop.stop()


def fake_env(storage_type, sd_version=3, data_center=None,
             remote_path="server:/path"):
    if storage_type == 'file':
        return fake_file_env(
            sd_version=sd_version,
            data_center=data_center,
            remote_path=remote_path)
    elif storage_type == 'block':
        return fake_block_env(sd_version=sd_version, data_center=data_center)
    else:
        raise ValueError("Invalid storage_type: %r" % storage_type)


@contextmanager
def fake_volume(storage_type='file', size=MiB, format=sc.RAW_FORMAT,
                legal=True):
    img_id = make_uuid()
    vol_id = make_uuid()
    with fake_env(storage_type) as env:
        env.make_volume(size, img_id, vol_id, vol_format=format, legal=legal)
        vol = env.sd_manifest.produceVolume(img_id, vol_id)
        yield vol


class FakeMetadata(dict):
    @contextmanager
    def transaction(self):
        yield


class FakeVolume(object):

    def __init__(self):
        # Test may set errors here to make method calls raise expected or
        # unexpected errors.
        self.errors = {}

    @maybefail
    @recorded
    def reduce(self, size):
        pass


class FakeSD(object):
    def __init__(self, sd_manifest, lvm=None):
        self._manifest = sd_manifest
        self.lvm = lvm
        self.volumes = {}

    @property
    def manifest(self):
        return self._manifest

    def produceVolume(self, img_id, vol_id):
        key = (img_id, vol_id)
        if key not in self.volumes:
            raise se.VolumeDoesNotExist(vol_id)
        return self.volumes[key]

    def getVersion(self):
        return self._manifest.getVersion()

    def extendVolume(self, volumeUUID, size):
        if self.lvm:
            self.lvm.extendLV(self._manifest.sdUUID, volumeUUID, size)

    def qcow2_compat(self):
        return self._manifest.qcow2_compat()


def make_sd_metadata(sduuid, version=3, dom_class=sd.DATA_DOMAIN, pools=None):
    md = FakeMetadata()
    md[sd.DMDK_SDUUID] = sduuid
    md[sd.DMDK_VERSION] = version
    md[sd.DMDK_CLASS] = dom_class
    md[sd.DMDK_POOLS] = pools if pools is not None else [make_uuid()]
    if version > 4:
        md[sd.DMDK_ALIGNMENT] = sc.ALIGNMENT_1M
        md[sd.DMDK_BLOCK_SIZE] = sc.BLOCK_SIZE_512
    return md


def make_blocksd_manifest(tmpdir, fake_lvm, sduuid=None, devices=None,
                          sd_version=3):
    if sduuid is None:
        sduuid = make_uuid()
    if devices is None:
        devices = get_random_devices()
    spuuid = make_uuid()

    fake_lvm.createVG(sduuid, devices, blockSD.STORAGE_DOMAIN_TAG,
                      blockSD.VG_METADATASIZE)
    fake_lvm.createLV(sduuid, sd.METADATA, blockSD.SD_METADATA_SIZE)

    # Create the rest of the special LVs
    bsd = blockSD.BlockStorageDomain
    special = bsd.special_volumes(sd_version)
    sizes_mb = bsd.special_volumes_size_mb(sc.ALIGNMENT_1M)
    for name, size_mb in six.iteritems(sizes_mb):
        if name in special:
            fake_lvm.createLV(sduuid, name, size_mb)

    fake_lvm.createLV(sduuid, blockSD.MASTERLV, blockSD.MASTER_LV_SIZE_MB)

    # We'll store the domain metadata in the VG's tags
    metadata = make_sd_metadata(sduuid, version=sd_version, pools=[spuuid])
    assert(metadata[sd.DMDK_VERSION] >= 3)  # Tag based MD is V3 and above
    tag_md = blockSD.TagBasedSDMetadata(sduuid)
    tag_md.update(metadata)

    manifest = blockSD.BlockStorageDomainManifest(sduuid, tag_md)
    os.makedirs(os.path.join(manifest.domaindir, sd.DOMAIN_IMAGES))

    # Make the repo directory structure
    repo_pool_dir = os.path.join(tmpdir, spuuid)
    os.mkdir(repo_pool_dir)
    os.symlink(manifest.domaindir, os.path.join(repo_pool_dir, sduuid))
    return manifest


def get_random_devices(count=NR_PVS):
    return ['/dev/mapper/{0}'.format(binascii.hexlify(os.urandom(16)))
            for _ in range(count)]


def get_metafile_path(domaindir):
    return os.path.join(domaindir, sd.DOMAIN_META_DATA, sd.METADATA)


def make_filesd_manifest(mnt_dir, sd_version=3):
    spuuid = make_uuid()
    sduuid = make_uuid()

    domain_path = os.path.join(mnt_dir, sduuid)
    metafile = get_metafile_path(domain_path)
    make_file(metafile)
    metadata = fileSD.FileSDMetadata(metafile)
    metadata.update(make_sd_metadata(sduuid, version=sd_version,
                                     pools=[spuuid]))

    manifest = fileSD.FileStorageDomainManifest(domain_path, metadata)
    os.makedirs(os.path.join(manifest.domaindir, sd.DOMAIN_IMAGES))
    return manifest


def make_file_volume(sd_manifest, size, imguuid, voluuid,
                     parent_vol_id=sc.BLANK_UUID,
                     vol_format=sc.RAW_FORMAT,
                     vol_type=sc.LEAF_VOL,
                     prealloc=sc.SPARSE_VOL,
                     disk_type=sc.DATA_DISKTYPE,
                     desc='fake volume', qcow2_compat='0.10',
                     legal=True):
    volpath = os.path.join(sd_manifest.domaindir, "images", imguuid, voluuid)

    # Create needed path components.
    make_file(volpath, size)

    # Create the image.
    if vol_format == sc.COW_FORMAT:
        backing = parent_vol_id if parent_vol_id != sc.BLANK_UUID else None
        if backing:
            backing_path = os.path.join(
                sd_manifest.domaindir, "images", imguuid, backing)
            backing_format = qemuimg.info(backing_path)["format"]
        else:
            backing_format = None

        op = qemuimg.create(
            volpath,
            size=size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat=qcow2_compat,
            backing=backing,
            backingFormat=backing_format)
        op.run()
    else:
        # TODO: Use fallocate helper like the real code.
        if prealloc == sc.PREALLOCATED_VOL:
            preallocation = qemuimg.PREALLOCATION.FALLOC
        else:
            preallocation = None
        op = qemuimg.create(
            volpath,
            size=size,
            format=qemuimg.FORMAT.RAW,
            preallocation=preallocation)
        op.run()

    # Create meta files.
    mdfiles = [volpath + '.meta', volpath + '.lease']
    for mdfile in mdfiles:
        make_file(mdfile)

    vol_class = sd_manifest.getVolumeClass()
    vol_class.newMetadata(
        (volpath,),
        sd_manifest.sdUUID,
        imguuid,
        parent_vol_id,
        size,
        sc.type2name(vol_format),
        sc.type2name(prealloc),
        sc.type2name(vol_type),
        disk_type,
        desc,
        sc.LEGAL_VOL if legal else sc.ILLEGAL_VOL)


def make_block_volume(lvm, sd_manifest, size, imguuid, voluuid,
                      parent_vol_id=sc.BLANK_UUID,
                      vol_format=sc.RAW_FORMAT,
                      vol_type=sc.LEAF_VOL,
                      prealloc=sc.PREALLOCATED_VOL,
                      disk_type=sc.DATA_DISKTYPE,
                      desc='fake volume', qcow2_compat='0.10', legal=True):
    sduuid = sd_manifest.sdUUID
    imagedir = sd_manifest.getImageDir(imguuid)
    if not os.path.exists(imagedir):
        os.makedirs(imagedir)

    lv_size = sd_manifest.getVolumeClass().calculate_volume_alloc_size(
        prealloc, vol_format, size, None)
    lv_size_mb = (utils.round(lv_size, MiB) // MiB)
    lvm.createLV(sduuid, voluuid, lv_size_mb)

    # LVM may create the volume with a larger size due to extent granularity
    if vol_format == sc.RAW_FORMAT:
        lv_size = int(lvm.getLV(sduuid, voluuid).size)
        if lv_size > size:
            size = lv_size

    if vol_format == sc.COW_FORMAT:
        volpath = lvm.lvPath(sduuid, voluuid)
        backing = parent_vol_id if parent_vol_id != sc.BLANK_UUID else None

        if backing:
            backing_path = lvm.lvPath(sduuid, backing)
            backing_format = qemuimg.info(backing_path)["format"]
        else:
            backing_format = None

        # Write qcow2 image to the fake block device - truncating the file.
        op = qemuimg.create(
            volpath,
            size=size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat=qcow2_compat,
            backing=backing,
            backingFormat=backing_format)
        op.run()

        # Truncate fake block device back ot the proper size.
        with open(volpath, "r+") as f:
            f.truncate(int(lvm.getLV(sduuid, voluuid).size))

    with sd_manifest.acquireVolumeMetadataSlot(voluuid) as slot:
        add_tags = [
            "%s%s" % (sc.TAG_PREFIX_MD, slot),
            "%s%s" % (sc.TAG_PREFIX_PARENT, parent_vol_id),
            "%s%s" % (sc.TAG_PREFIX_IMAGE, imguuid),
        ]
        lvm.changeLVsTags(sduuid, (voluuid,), addTags=add_tags)

    vol_class = sd_manifest.getVolumeClass()
    vol_class.newMetadata(
        (sduuid, slot),
        sduuid,
        imguuid,
        parent_vol_id,
        size,
        sc.type2name(vol_format),
        sc.type2name(prealloc),
        sc.type2name(vol_type),
        disk_type,
        desc,
        sc.LEGAL_VOL if legal else sc.ILLEGAL_VOL)


def write_qemu_chain(vol_list):
    # Starting with the base volume in vol_list, write to the chain in a
    # pattern like the following:
    #
    #  logical offset: 0K            1K            2K            3K
    #   Base Volume 0: 0xf0 0xf0 ...
    #               1:               0xf1 0xf1 ...
    #               2:                             0xf2 0xf2 ...
    #   Leaf Volume 3:                                           0xf3 0xf3 ...
    # This allows us to verify the integrity of the whole chain.
    for i, vol in enumerate(vol_list):
        vol_fmt = sc.fmt2str(vol.getFormat())
        offset = i * KiB
        pattern = 0xf0 + i
        qemuio.write_pattern(
            vol.volumePath,
            vol_fmt,
            offset=offset,
            len=KiB,
            pattern=pattern)


def verify_qemu_chain(vol_list):
    # Check the integrity of a volume chain by reading the leaf volume
    # and verifying the pattern written by write_chain.  Also, check each
    # volume in the chain to ensure it contains the correct data.
    top_vol = vol_list[-1]
    top_vol_fmt = sc.fmt2str(top_vol.getFormat())
    for i, vol in enumerate(vol_list):
        offset = i * KiB
        pattern = 0xf0 + i

        # Check that the correct pattern can be read through the top volume
        qemuio.verify_pattern(
            top_vol.volumePath,
            top_vol_fmt,
            offset=offset,
            len=KiB,
            pattern=pattern)

        # Check the volume where the pattern was originally written
        vol_fmt = sc.fmt2str(vol.getFormat())
        qemuio.verify_pattern(
            vol.volumePath,
            vol_fmt,
            offset=offset,
            len=KiB,
            pattern=pattern)

        # Check that the next offset contains zeroes.  If we know this layer
        # has zeroes at next_offset we can be sure that data read at the same
        # offset in the next layer belongs to that layer.
        next_offset = (i + 1) * KiB
        qemuio.verify_pattern(
            vol.volumePath,
            vol_fmt,
            offset=next_offset,
            len=KiB,
            pattern=0)


def make_qemu_chain(env, size, base_vol_fmt, chain_len,
                    qcow2_compat='0.10', prealloc=sc.SPARSE_VOL):
    vol_list = []
    img_id = make_uuid()
    parent_vol_id = sc.BLANK_UUID
    vol_fmt = base_vol_fmt
    for i in range(chain_len):
        vol_id = make_uuid()
        if parent_vol_id != sc.BLANK_UUID:
            vol_fmt = sc.COW_FORMAT
        vol_type = sc.LEAF_VOL if i == chain_len - 1 else sc.INTERNAL_VOL
        env.make_volume(size, img_id, vol_id,
                        parent_vol_id=parent_vol_id, vol_format=vol_fmt,
                        vol_type=vol_type, prealloc=prealloc,
                        qcow2_compat=qcow2_compat)
        vol = env.sd_manifest.produceVolume(img_id, vol_id)
        vol_list.append(vol)
        parent_vol_id = vol_id
    return vol_list


class FakeGuardedLock(guarded.AbstractLock):
    def __init__(self, ns, name, mode, log, acquire=None, release=None):
        self._ns = ns
        self._name = name
        self._mode = mode
        self._log = log
        self._acquire_err = acquire
        self._release_err = release

    @property
    def ns(self):
        return self._ns

    @property
    def name(self):
        return self._name

    @property
    def mode(self):
        return self._mode

    def acquire(self):
        if self._acquire_err:
            raise self._acquire_err()
        entry = ('acquire', self._ns, self._name, self._mode)
        self._log.append(entry)

    def release(self):
        if self._release_err:
            raise self._release_err()
        entry = ('release', self._ns, self._name, self._mode)
        self._log.append(entry)


class Aborting(object):
    def __init__(self, count=1):
        self.count = count

    def __call__(self):
        self.count -= 1
        return self.count < 0


class Callable(object):

    def __init__(self, hang_timeout=0, result=None):
        self._hang_timeout = hang_timeout
        self._result = result
        self._running = threading.Event()
        self._blocking = threading.Event()
        self._done = threading.Event()
        self.args = None

    def __call__(self, args=None):
        self.args = args
        self._running.set()
        log.info("callable is running")
        if self._hang_timeout:
            log.info("callable is hung (timeout=%s)", self._hang_timeout)
            if not self._blocking.wait(self._hang_timeout):
                raise RuntimeError("Timeout waiting for task switch off")

        self._done.set()
        log.info("callable is finished")
        if isinstance(self._result, Exception):
            raise self._result

        return self._result

    def finish(self, timeout=WAIT_TIMEOUT):
        self._blocking.set()
        log.info("finishing callable (timeout=%s)", timeout)
        if not self._done.wait(timeout):
            raise RuntimeError("Timeout waiting for task completion")

    def wait_until_running(self, timeout=WAIT_TIMEOUT):
        log.info("waiting for callable to run (timeout=%s)", timeout)
        if not self._running.wait(timeout):
            raise RuntimeError("Timeout waiting for task to start")

    def was_called(self):
        return self._running.is_set()

    def is_finished(self):
        return self._done.is_set()


@contextmanager
def chmod(path, mode):
    """
    Changes path permissions.

    Change the permissions of path to the numeric mode before entering the
    context, and restore the original value when exiting from the context.

    Arguments:
        path (str): file/directory path
        mode (int): new mode
    """

    orig_mode = stat.S_IMODE(os.stat(path).st_mode)

    os.chmod(path, mode)
    try:
        yield
    finally:
        try:
            os.chmod(path, orig_mode)
        except Exception as e:
            logging.error("Failed to restore %r mode: %s", path, e)
