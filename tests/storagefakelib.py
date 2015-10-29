# Copyright 2015 Red Hat, Inc.
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
from copy import deepcopy

from storage import lvm as real_lvm


class FakeLVM(object):
    _PV_SIZE = 10 << 30             # We pretend all PVs are 10G in size
    _PV_PE_SIZE = 128 << 20         # Found via inspection of real environment
    _PV_MDA_COUNT = 2               # The number of PEs used for metadata areas
    _PV_UNUSABLE = (_PV_PE_SIZE *   # 2 PE for metadata + 1 PE to hold a header
                    (1 + _PV_MDA_COUNT))

    def __init__(self, root):
        self.root = root
        os.mkdir(os.path.join(self.root, 'dev'))
        self.pvmd = {}
        self.vgmd = {}
        self.lvmd = {}

    def createVG(self, vgName, devices, initialTag, metadataSize,
                 extentsize=128, force=False):
        # Convert params from MB to bytes to match other fields
        metadataSize <<= 20
        extentsize <<= 20

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

    def createLV(self, vgName, lvName, size, activate=True, contiguous=False,
                 initialTag=None):
        lv_attr = dict(voltype='-',
                       permission='w',
                       allocations='i',
                       fixedminor='-',
                       state='a',
                       devopen='-',
                       target='-',
                       zero='-')
        lv_md = dict(uuid=fake_lvm_uuid(),
                     name=lvName,
                     vg_name=vgName,
                     attr=lv_attr,
                     size=str(size),
                     seg_start_pe='0',
                     devices='',
                     tags=[],
                     writeable=True,
                     opened=False,
                     active=True)

        vg_dict = self.lvmd.setdefault(vgName, {})
        vg_dict[lvName] = lv_md

    def activateLVs(self, vgName, lvNames):
        pass

    def addtag(self, vg, lv, tag):
        self.lvmd[vg][lv]['tags'].append(tag)

    def lvPath(self, vgName, lvName):
        return os.path.join(self.root, "dev", vgName, lvName)

    def getPV(self, pvName):
        md = deepcopy(self.pvmd[pvName])
        return real_lvm.PV(**md)

    def getVG(self, vgName):
        vg_md = deepcopy(self.vgmd[vgName])
        vg_attr = real_lvm.VG_ATTR(**vg_md['attr'])
        vg_md['attr'] = vg_attr
        return real_lvm.VG(**vg_md)

    def _getLV(self, vgName, lvName):
        lv_md = deepcopy(self.lvmd[vgName][lvName])
        lv_attr = real_lvm.LV_ATTR(**lv_md['attr'])
        lv_md['attr'] = lv_attr
        return real_lvm.LV(**lv_md)

    def getLV(self, vgName, lvName=None):
        if lvName is None:
            return [self._getLV(vgName, lv_name)
                    for lv_name in self.lvmd[vgName].keys()]
        else:
            return self._getLV(vgName, lvName)

    def fake_lv_symlink_create(self, vg_name, lv_name):
        volpath = self.lvPath(vg_name, lv_name)
        os.makedirs(os.path.dirname(volpath))
        with open(volpath, "w") as f:
            f.truncate(int(self.lvmd[vg_name][lv_name]['size']))

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
                     dev_size=str(self._PV_SIZE))
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
