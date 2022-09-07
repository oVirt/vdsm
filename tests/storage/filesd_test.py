# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import fnmatch
import os
import time
import uuid

import pytest

from storage.storagefakelib import fake_repo
from testlib import VdsmTestCase
from testlib import expandPermutations
from testlib import namedTemporaryDir
from testlib import permutations

from vdsm.storage import constants as sc
from vdsm.storage import fileSD
from vdsm.storage import fileUtils
from vdsm.storage import outOfProcess as oop
from vdsm.storage import sd


class FileStorageDomainManifest(fileSD.FileStorageDomainManifest):

    def __init__(self, domainpath, oop):
        self.mountpoint = os.path.dirname(domainpath)
        self.sdUUID = os.path.basename(domainpath)
        self._oop = oop

    @property
    def oop(self):
        return self._oop


class FileStorageDomain(fileSD.FileStorageDomain):

    stat = None  # Accessed in __del__

    def __init__(self, uuid, mountpoint, oop):
        domainpath = os.path.join(mountpoint, uuid)
        self._manifest = FileStorageDomainManifest(domainpath, oop)


class FakeGlob(object):

    def __init__(self, files):
        self.files = files

    def glob(self, pattern):
        return fnmatch.filter(self.files, pattern)


class FakeOOP(object):

    def __init__(self, glob=None):
        self.glob = glob


class TestGetAllVolumes(VdsmTestCase):

    MOUNTPOINT = "/rhev/data-center/%s" % uuid.uuid4()
    SD_UUID = str(uuid.uuid4())
    IMAGES_DIR = os.path.join(MOUNTPOINT, SD_UUID, sd.DOMAIN_IMAGES)

    def test_no_volumes(self):
        oop = FakeOOP(FakeGlob([]))
        dom = FileStorageDomain(self.SD_UUID, self.MOUNTPOINT, oop)
        res = dom.getAllVolumes()
        self.assertEqual(res, {})

    def test_no_templates(self):
        oop = FakeOOP(FakeGlob([
            os.path.join(self.IMAGES_DIR, "image-1", "volume-1.meta"),
            os.path.join(self.IMAGES_DIR, "image-1", "volume-2.meta"),
            os.path.join(self.IMAGES_DIR, "image-1", "volume-3.meta"),
            os.path.join(self.IMAGES_DIR, "image-2", "volume-4.meta"),
            os.path.join(self.IMAGES_DIR, "image-2", "volume-5.meta"),
            os.path.join(self.IMAGES_DIR, "image-3", "volume-6.meta"),
        ]))
        dom = FileStorageDomain(self.SD_UUID, self.MOUNTPOINT, oop)
        res = dom.getAllVolumes()

        # These volumes should have parent uuid, but the implementation does
        # not read the meta data files, so this info is not available (None).
        self.assertEqual(res, {
            "volume-1": (("image-1",), None),
            "volume-2": (("image-1",), None),
            "volume-3": (("image-1",), None),
            "volume-4": (("image-2",), None),
            "volume-5": (("image-2",), None),
            "volume-6": (("image-3",), None),
        })

    def test_with_template(self):
        oop = FakeOOP(FakeGlob([
            os.path.join(self.IMAGES_DIR, "template-1", "volume-1.meta"),
            os.path.join(self.IMAGES_DIR, "image-1", "volume-1.meta"),
            os.path.join(self.IMAGES_DIR, "image-1", "volume-2.meta"),
            os.path.join(self.IMAGES_DIR, "image-1", "volume-3.meta"),
            os.path.join(self.IMAGES_DIR, "image-2", "volume-1.meta"),
            os.path.join(self.IMAGES_DIR, "image-2", "volume-4.meta"),
            os.path.join(self.IMAGES_DIR, "image-3", "volume-5.meta"),
        ]))
        dom = FileStorageDomain(self.SD_UUID, self.MOUNTPOINT, oop)
        res = dom.getAllVolumes()

        self.assertEqual(len(res), 5)

        # The template image must be first - we have code assuming this.
        self.assertEqual(res["volume-1"].imgs[0], "template-1")

        # The rest of the images have random order.
        self.assertEqual(sorted(res["volume-1"].imgs[1:]),
                         ["image-1", "image-2"])

        # For template volumes we have parent info.
        self.assertEqual(res["volume-1"].parent, sd.BLANK_UUID)

        self.assertEqual(res["volume-2"], (("image-1",), None))
        self.assertEqual(res["volume-3"], (("image-1",), None))
        self.assertEqual(res["volume-4"], (("image-2",), None))
        self.assertEqual(res["volume-5"], (("image-3",), None))

    @pytest.mark.skipif(
        "OVIRT_CI" in os.environ or "TRAVIS_CI" in os.environ,
        reason="performance test, unpredictable on CI")
    def test_scale(self):
        # For this test we want real world strings
        images_count = 5000
        template_image_uuid = str(uuid.uuid4())
        template_volume_uuid = str(uuid.uuid4())

        files = []
        template_volume = os.path.join(self.IMAGES_DIR, template_image_uuid,
                                       template_volume_uuid + ".meta")
        files.append(template_volume)

        for i in range(images_count):
            image_uuid = str(uuid.uuid4())
            volume_uuid = str(uuid.uuid4())
            template_volume = os.path.join(self.IMAGES_DIR, image_uuid,
                                           template_volume_uuid + ".meta")
            files.append(template_volume)
            new_volume = os.path.join(self.IMAGES_DIR, image_uuid,
                                      volume_uuid + ".meta")
            files.append(new_volume)

        oop = FakeOOP(FakeGlob(files))
        dom = FileStorageDomain(self.SD_UUID, self.MOUNTPOINT, oop)

        start = time.time()
        dom.getAllVolumes()
        elapsed = time.time() - start
        print("%f seconds" % elapsed)

        # This takes about 0.11 seconds on T450s.
        self.assertTrue(elapsed < 0.5, "Elapsed time: %f seconds" % elapsed)


SDInfo = collections.namedtuple("SDInfo",
                                "uuid, remote_path, mountpoint, dom_dir")


class TestGetStorageDomainsList(VdsmTestCase):

    def test_no_sd(self):
        with fake_repo():
            self.assertEqual(fileSD.getStorageDomainsList(), [])

    def test_detect_filesd(self):
        with fake_repo() as repo:
            sd = add_filesd(repo, "server:/path", str(uuid.uuid4()))
            self.assertEqual(fileSD.getStorageDomainsList(), [sd.uuid])


@expandPermutations
class TestScanDomains(VdsmTestCase):

    def tearDown(self):
        # scanDomains is implemented using oop, leaving stale ioprocess child
        # processes.
        oop.stop()

    def test_no_sd(self):
        with fake_repo():
            self.assertEqual(list(fileSD.scanDomains()), [])

    @permutations([
        # scan_pattern, sd_type
        (os.path.join(sd.GLUSTERSD_DIR, "*"), "gluster"),
        ("_*", "local"),
        ("*", "all"),
    ])
    def test_select_domains(self, scan_pattern, sd_type):
        with fake_repo() as repo, namedTemporaryDir() as tmpdir:
            file_sd = add_filesd(repo, "nfs.server:/path", str(uuid.uuid4()))
            gluster_sd = add_filesd(repo, "gluster.server:/volname",
                                    str(uuid.uuid4()), subdir=sd.GLUSTERSD_DIR)
            local_sd = add_localsd(repo, tmpdir, str(uuid.uuid4()))
            domains = {"gluster": [gluster_sd],
                       "local": [local_sd],
                       "all": [file_sd, gluster_sd, local_sd]}
            expected = set([(domain.uuid, domain.dom_dir)
                            for domain in domains[sd_type]])
            self.assertEqual(set(fileSD.scanDomains(scan_pattern)), expected)

    def test_nfs_with_IPV6_address(self):
        with fake_repo() as repo:
            nfs_sd = add_filesd(repo, "[201::1]:/path", str(uuid.uuid4()))
            self.assertEqual(list(fileSD.scanDomains()),
                             [(nfs_sd.uuid, nfs_sd.dom_dir)])


@expandPermutations
class TestVolumeOperations(VdsmTestCase):

    @permutations([
        # allow_active
        (True,),
        (False,),
    ])
    def test_reduce_volume(self, allow_active):
        oop = FakeOOP(FakeGlob([]))
        dom = FileStorageDomain("dummy_sd_uuid", "dummy_mountpoint", oop)
        dom.reduceVolume("dummy_img_uuid", "dummy_vol_uuuid",
                         allowActive=allow_active)


def add_filesd(repo, remote_path, sd_uuid, subdir=""):
    # Create mount directory in the repo
    mnt_dir = os.path.join(sc.REPO_MOUNT_DIR, subdir)
    local_path = fileUtils.transformPath(remote_path)
    mountpoint = os.path.join(mnt_dir, local_path)
    os.makedirs(mountpoint)
    return create_domain_structure(sd_uuid, remote_path, mountpoint)


def add_localsd(repo, tmpdir, sd_uuid):
    # Link local directory into the repo
    local_path = fileUtils.transformPath(tmpdir)
    mountpoint = os.path.join(sc.REPO_MOUNT_DIR, local_path)
    os.symlink(tmpdir, mountpoint)
    return create_domain_structure(sd_uuid, tmpdir, mountpoint)


def create_domain_structure(sd_uuid, remote_path, mountpoint):
    # Satisfy scanDomains
    dom_dir = os.path.join(mountpoint, sd_uuid)
    dom_md = os.path.join(dom_dir, sd.DOMAIN_META_DATA)
    os.makedirs(dom_md)
    return SDInfo(sd_uuid, remote_path, mountpoint, dom_dir)
