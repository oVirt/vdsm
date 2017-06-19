# Copyright 2015-2017 Red Hat, Inc.
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
import string
import random
from contextlib import contextmanager
from copy import deepcopy

import monkeypatch
from testlib import make_file, recorded
from testlib import namedTemporaryDir

from vdsm.storage import exception as se
from vdsm.storage import lvm as real_lvm
from vdsm.storage import resourceManager as rm
from vdsm.storage.constants import VG_EXTENT_SIZE_MB
from vdsm import utils

from storage import sd


class FakeLVM(object):
    # We pretend all PVs are 10G in size
    _PV_SIZE = 10 << 30
    # Found via inspection of real environment
    _PV_PE_SIZE = VG_EXTENT_SIZE_MB << 20
    # The number of PEs used for metadata areas
    _PV_MDA_COUNT = 2
    # 2 PE for metadata + 1 PE to hold a header
    _PV_UNUSABLE = (_PV_PE_SIZE * (1 + _PV_MDA_COUNT))

    def __init__(self, root):
        self.root = root
        os.mkdir(os.path.join(self.root, 'dev'))
        self.pvmd = {}
        self.vgmd = {}
        self.lvmd = {}

    def createVG(self, vgName, devices, initialTag, metadataSize, force=False):
        # Convert params from MB to bytes to match other fields
        metadataSize <<= 20
        extentsize = VG_EXTENT_SIZE_MB << 20

        for dev in devices:
            self._create_pv(dev, vgName, self._PV_SIZE)
        pv_name = (tuple(_fqpvname(pdev)
                         for pdev in real_lvm._normalizeargs(devices)))
        extent_count = self._calc_vg_pe_count(vgName)
        size = extent_count * self._PV_PE_SIZE

        vg_attr = dict(permission='w',
                       resizeable='z',
                       exported='-',
                       partial='-',
                       allocation='n',
                       clustered='-')
        vg_md = dict(uuid=fake_lvm_uuid(),
                     name=vgName,
                     attr=vg_attr,
                     size=str(size),
                     free=str(size),
                     extent_size=str(extentsize),
                     extent_count=str(extent_count),
                     free_count=str(extent_count),
                     tags=(initialTag,),
                     vg_mda_size=str(metadataSize),
                     vg_mda_free=None,
                     lv_count='0',
                     pv_count=str(len(devices)),
                     pv_name=pv_name,
                     writeable=True,
                     partial='OK')
        self.vgmd[vgName] = vg_md

        for dev in devices:
            self.pvmd[dev]['vg_uuid'] = vg_md['uuid']

    def invalidateVG(self, vgName):
        pass

    def _size_param_to_bytes(self, size):
        # Size is received as a string in MB.  We need to convert it to bytes
        # and round it up to a multiple of the VG extent size.
        extent_size = VG_EXTENT_SIZE_MB << 20
        size = int(size) << 20
        return utils.round(size, extent_size)

    def _create_lv_file(self, vgName, lvName, activate, size):
        # Create an LV as a regular file so we have a place to write data
        if activate:
            lv_path = self.lvPath(vgName, lvName)
        else:
            lv_path = self._lvPathInactive(vgName, lvName)
        make_file(lv_path, size)

    def createLV(self, vgName, lvName, size, activate=True, contiguous=False,
                 initialTags=()):
        try:
            vg_md = self.vgmd[vgName]
        except KeyError:
            raise se.CannotCreateLogicalVolume(vgName, lvName)

        size = self._size_param_to_bytes(size)

        # devices is hard to emulate properly (must have a PE allocator that
        # works the same as for LVM).  Since we don't need this value, use None
        devices = None

        state = 'a' if activate else '-'
        lv_attr = dict(voltype='-',
                       permission='w',
                       allocations='i',
                       fixedminor='-',
                       state=state,
                       devopen='-',
                       target='-',
                       zero='-')
        lv_md = dict(uuid=fake_lvm_uuid(),
                     name=lvName,
                     vg_name=vgName,
                     attr=lv_attr,
                     size=str(size),
                     seg_start_pe='0',
                     devices=devices,
                     tags=initialTags,
                     writeable=True,
                     opened=False,
                     active=bool(activate))

        lv_count = int(vg_md['lv_count']) + 1

        if (vgName, lvName) in self.lvmd:
            raise se.CannotCreateLogicalVolume(vgName, lvName)

        self.lvmd[(vgName, lvName)] = lv_md
        self.vgmd[vgName]['lv_count'] = str(lv_count)

        self._create_lv_file(vgName, lvName, activate, size)

    def activateLVs(self, vgName, lvNames):
        for lv in lvNames:
            try:
                lv_md = self.lvmd[(vgName, lv)]
            except KeyError as e:
                raise se.CannotActivateLogicalVolume(str(e))

            if not lv_md['active']:
                os.rename(self._lvPathInactive(vgName, lv),
                          self.lvPath(vgName, lv))
                lv_md['active'] = True
                lv_md['attr']['state'] = 'a'

    def deactivateLVs(self, vgName, lvNames):
        active_lvs = [lv for lv in lvNames
                      if self._is_lv_active(vgName, lv)]
        for lv in active_lvs:
            self._deactivate_lv(vgName, lv)

    def _is_lv_active(self, vg, lv):
        return os.path.exists(self.lvPath(vg, lv))

    def _deactivate_lv(self, vg, lv):
        lv_md = self.lvmd[(vg, lv)]
        os.rename(self.lvPath(vg, lv),
                  self._lvPathInactive(vg, lv))
        lv_md['active'] = False
        lv_md['attr']['state'] = '-'

    def addtag(self, vg, lv, tag):
        try:
            lv_md = self.lvmd[(vg, lv)]
        except KeyError:
            raise se.MissingTagOnLogicalVolume("%s/%s" % (vg, lv), tag)
        lv_md['tags'] += (tag,)

    def changeLVTags(self, vg, lv, delTags=(), addTags=()):
        try:
            lv_md = self.lvmd[(vg, lv)]
        except KeyError:
            raise se.LogicalVolumeReplaceTagError("LV %s does not exist",
                                                  "%s/%s" % (vg, lv))

        # Adding an existing tag or removing a nonexistent tag are ignored
        tags = set(lv_md['tags'])
        tags |= set(addTags)
        tags -= set(delTags)
        lv_md['tags'] = tuple(tags)

    def lvsByTag(self, vgName, tag):
        return [lv for lv in self.getLV(vgName) if tag in lv.tags]

    def lvPath(self, vgName, lvName):
        return os.path.join(self.root, "dev", vgName, lvName)

    def _lvPathInactive(self, vgName, lvName):
        # When creating a new LV we simulate it being inactive by adding an
        # extension so that it will not be visible.  We can then simulate
        # activation by renaming it.
        return self.lvPath(vgName, lvName) + '.inactive'

    def getPV(self, pvName):
        try:
            pv = self.pvmd[pvName]
        except KeyError:
            raise se.InaccessiblePhysDev((pvName,))
        md = deepcopy(pv)
        return real_lvm.PV(**md)

    def getVG(self, vgName):
        if vgName not in self.vgmd:
            raise se.VolumeGroupDoesNotExist(vgName)
        vg_md = deepcopy(self.vgmd[vgName])
        vg_attr = real_lvm.VG_ATTR(**vg_md['attr'])
        vg_md['attr'] = vg_attr
        return real_lvm.VG(**vg_md)

    def changeVGTags(self, vgName, delTags=(), addTags=()):
        try:
            vg_md = self.vgmd[vgName]
        except KeyError:
            raise se.VolumeGroupReplaceTagError("VG %s does not exist" %
                                                vgName)

        # Adding an existing tag or removing a nonexistent tag are ignored
        tags = set(vg_md['tags'])
        tags |= set(addTags)
        tags -= set(delTags)
        vg_md['tags'] = tuple(tags)

    def _getLV(self, vgName, lvName):
        try:
            lv = self.lvmd[(vgName, lvName)]
        except KeyError:
            raise se.LogicalVolumeDoesNotExistError("%s/%s" % (vgName, lvName))
        lv_md = deepcopy(lv)
        lv_attr = real_lvm.LV_ATTR(**lv_md['attr'])
        lv_md['attr'] = lv_attr
        return real_lvm.LV(**lv_md)

    def getLV(self, vgName, lvName=None):
        if lvName is None:
            return [self._getLV(vgName, lv)
                    for vg, lv in self.lvmd if vg == vgName]
        else:
            return self._getLV(vgName, lvName)

    def extendLV(self, vgName, lvName, size_mb):
        try:
            lv = self.lvmd[(vgName, lvName)]
        except KeyError:
            raise se.LogicalVolumeExtendError(vgName, lvName,
                                              "%sm" % (size_mb,))
        size = self._size_param_to_bytes(size_mb)
        current_size = int(lv["size"])
        if current_size >= size:
            # In real lvm code we call call lvm and check after the call if the
            # lvm is already in the corect size.
            return
        lv['size'] = str(size)
        self._create_lv_file(vgName, lvName, lv['active'], size)
        # TODO: vg free extent accounting

    def fake_lv_symlink_create(self, vg_name, lv_name):
        volpath = self.lvPath(vg_name, lv_name)
        with open(volpath, "w") as f:
            f.truncate(int(self.lvmd[(vg_name, lv_name)]['size']))

    def _create_pv(self, pv_name, vg_name, size):
        # pe_start is difficult to calculate correctly but since it's not
        # currently needed by users of FakeLVM, set it to None.
        pe_start = None
        pe_count = (size - self._PV_UNUSABLE) / self._PV_PE_SIZE
        pv_md = dict(uuid=fake_lvm_uuid(),
                     name='/dev/mapper/%s' % pv_name,
                     guid=pv_name,
                     size=str(pe_count * self._PV_PE_SIZE),
                     vg_name=vg_name,
                     vg_uuid=None,  # This is set when the VG is created
                     pe_start=pe_start,
                     pe_count=str(pe_count),
                     pe_alloc_count='0',
                     mda_count=str(self._PV_MDA_COUNT),
                     dev_size=str(self._PV_SIZE),
                     mda_used_count='0')
        self.pvmd[pv_name] = pv_md

    def _calc_vg_pe_count(self, vg_name):
        return sum(int(pv["pe_count"]) for pv in self.pvmd.values()
                   if pv["vg_name"] == vg_name)


_fqpvname = real_lvm._fqpvname


def fake_lvm_uuid():
    chars = string.ascii_letters + string.digits

    def part(size):
        return ''.join(random.choice(chars) for _ in range(size))
    return '-'.join(part(size) for size in [6, 4, 4, 4, 4, 6])


class FakeResourceManager(object):

    SHARED = rm.SHARED
    EXCLUSIVE = rm.EXCLUSIVE

    @recorded
    @contextmanager
    def acquireResource(self, *args, **kwargs):
        try:
            yield
        finally:
            self.releaseResource(*args, **kwargs)

    @recorded
    def releaseResource(self, *args, **kwargs):
        pass

    def getNamespace(self, *args):
        return "_".join(args)


class FakeStorageDomainCache(object):

    def __init__(self):
        self.domains = {}

    def produce(self, sdUUID):
        try:
            return self.domains[sdUUID]
        except KeyError:
            raise se.StorageDomainDoesNotExist(sdUUID)

    def produce_manifest(self, sdUUID):
        return self.produce(sdUUID).manifest

    def manuallyRemoveDomain(self, sdUUID):
        self.domains.pop(sdUUID, None)


class fake_guarded_context(object):

    def __init__(self):
        self.locks = None

    def __call__(self, locks):
        self.locks = locks
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


@contextmanager
def fake_repo():
    """
    Create a temporary repository and monkeypatch the system to use it instead
    of /rhev/data-center.
    """
    with namedTemporaryDir() as repo:
        # /rhev/data-center/mnt
        mnt_dir = os.path.join(repo, sd.DOMAIN_MNT_POINT)
        os.mkdir(mnt_dir)
        # /rhev/data-center/mnt/blockSD
        mnt_blocksd_dir = os.path.join(mnt_dir, sd.BLOCKSD_DIR)
        os.mkdir(mnt_blocksd_dir)
        # /rhev/data-center/mnt/glusterSD
        mnt_glustersd_dir = os.path.join(mnt_dir, sd.GLUSTERSD_DIR)
        os.mkdir(mnt_glustersd_dir)
        with monkeypatch.MonkeyPatchScope([
            (sd.StorageDomain, 'storage_repository', repo),
        ]):
            yield repo
