#
# Copyright 2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

"""
This module implements a job that runs virt-sysprep utility over all disks of a
VM template just after it was created and before it may be used to create VMs.
This process is called 'sealing' the template.

Note that virt-sysprep works on all disks together, not disk-by-disk. So we
need first to create all disks for the template and after that pass all of them
together to virt-sysprep.

The following steps are performed when creating a template:

0. Create the template.
1. Create all template disks as regular (LEAF) disks.
2. Make the disks ILLEGAL.
3. Seal the template (run virt-sysprep on the disks).
4. Make the disks LEGAL and SHARED.

If anything fails in the middle of the process, the whole process fails and the
template is removed. If for some reason the disks are not removed after
failure, they will be still ILLEGAL, so nobody can use them. But because of
this volumes on the step (3) are ILLEGAL, that's why allowIllegal=True
parameter is passed to prepareImage().
"""

from __future__ import absolute_import
from __future__ import division

from vdsm import virtsysprep
from vdsm.common import properties
from vdsm.virt.utils import prepared
import vdsm.virt.jobs


class ImagePreparingError(Exception):
    ''' Error preparing image '''


class ImageTearingDownError(Exception):
    ''' Error tearing down image '''


class Job(vdsm.virt.jobs.Job):

    def __init__(self, vm_id, job_id, sp_id, images, irs):
        super(Job, self).__init__(job_id, 'seal_vm')
        self._vm_id = vm_id
        self._images = [
            SealImageInfo(image, sp_id, irs)
            for image in images
        ]

    def _run(self):
        with prepared(self._images):
            vol_paths = [image_info.path for image_info in self._images]
            virtsysprep.sysprep(self._vm_id, vol_paths)


class SealImageInfo(properties.Owner):
    sd_id = properties.UUID(required=True)
    img_id = properties.UUID(required=True)
    vol_id = properties.UUID(required=True)

    def __init__(self, params, sp_id, irs):
        self.sd_id = params.get('sd_id')
        self.img_id = params.get('img_id')
        self.vol_id = params.get('vol_id')
        self._sp_id = sp_id
        self._irs = irs
        self._path = None

    @property
    def path(self):
        return self._path

    def prepare(self):
        res = self._irs.prepareImage(self.sd_id,
                                     self._sp_id,
                                     self.img_id,
                                     self.vol_id,
                                     allowIllegal=True)
        if res['status']['code']:
            raise ImagePreparingError("Cannot prepare image %s: %s" %
                                      (self, res['status']['message']))

        self._path = res['path']

    def teardown(self):
        res = self._irs.teardownImage(self.sd_id,
                                      self._sp_id,
                                      self.img_id)
        if res['status']['code']:
            raise ImageTearingDownError("Cannot tear down image %s: %s" %
                                        (self, res['status']['message']))

    def __repr__(self):
        return ("<%s sd_id=%s img_id=%s vol_id=%s at 0x%s>" %
                (self.__class__.__name__, self.sd_id, self.img_id, self.vol_id,
                 id(self)))
