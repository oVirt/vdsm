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
    _PV_SIZE = 10 << 30  # We pretend all PVs contribute 10G of space

    def __init__(self, root):
        self.root = root
        os.mkdir(os.path.join(self.root, 'dev'))
        self.vgmd = {}
        self.lvmd = {}

    def createVG(self, vgName, devices, initialTag, metadataSize,
                 extentsize=134217728, force=False):
        devices = [_fqpvname(dev) for dev in devices]
        size = self._PV_SIZE * len(devices)
        extent_count = size / extentsize

        vg_attr = dict(permission='w',
                       resizeable='z',
                       exported='-',
                       partial='-',
                       allocation='n',
                       clustered='-')
        vg_md = dict(uuid=fake_lvm_uuid(),
                     name=vgName,
                     attr=vg_attr,
                     size=size,
                     free=size,
                     extent_size=extentsize,
                     extent_count=extent_count,
                     free_count=extent_count,
                     tags=[initialTag],
                     vg_mda_size=metadataSize,
                     vg_mda_free=metadataSize,
                     lv_count='0',
                     pv_count=len(devices),
                     pv_name=tuple(devices),
                     writeable=True,
                     partial='OK')
        self.vgmd[vgName] = vg_md

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
                     tags=(),
                     writeable=True,
                     opened=False,
                     active=True)

        vg_dict = self.lvmd.setdefault(vgName, {})
        vg_dict[lvName] = lv_md

    def activateLVs(self, vgName, lvNames):
        pass

    def lvPath(self, vgName, lvName):
        return os.path.join(self.root, "dev", vgName, lvName)

    def getVG(self, vgName):
        vg_md = deepcopy(self.vgmd[vgName])
        vg_attr = real_lvm.VG_ATTR(**vg_md['attr'])
        vg_md['attr'] = vg_attr
        return real_lvm.VG(**vg_md)

    def getLV(self, vgName, lvName):
        lv_md = deepcopy(self.lvmd[vgName][lvName])
        lv_attr = real_lvm.LV_ATTR(**lv_md['attr'])
        lv_md['attr'] = lv_attr
        return real_lvm.LV(**lv_md)

    def fake_lv_symlink_create(self, vg_name, lv_name):
        volpath = self.lvPath(vg_name, lv_name)
        os.makedirs(os.path.dirname(volpath))
        with open(volpath, "w") as f:
            f.truncate(int(self.lvmd[vg_name][lv_name]['size']))

_fqpvname = real_lvm._fqpvname


def fake_lvm_uuid():
    chars = string.ascii_letters + string.digits

    def part(size):
        return ''.join(random.choice(chars) for _ in range(size))
    return '-'.join(part(size) for size in [6, 4, 4, 4, 4, 6])
