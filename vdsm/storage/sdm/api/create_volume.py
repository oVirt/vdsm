#
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

from vdsm import exception
from vdsm.storage import exception as se

from storage import resourceManager as rm
from storage import image, sd, volume
from storage.resourceFactories import IMAGE_NAMESPACE

from . import base

rmanager = rm.ResourceManager.getInstance()


class Job(base.Job):
    def __init__(self, job_id, host_id, sd_manifest, vol_info):
        super(Job, self).__init__(job_id, 'create_volume', host_id)
        self.sd_manifest = sd_manifest
        self.vol_info = vol_info

    def _run(self):
        vol_format = volume.name2type(self.vol_info.vol_format)

        with self.sd_manifest.domain_lock(self.host_id):
            image_res_ns = sd.getNamespace(self.sd_manifest.sdUUID,
                                           IMAGE_NAMESPACE)
            with rmanager.acquireResource(image_res_ns, self.vol_info.img_id,
                                          rm.LockType.exclusive):
                artifacts = self.sd_manifest.get_volume_artifacts(
                    self.vol_info.img_id, self.vol_info.vol_id)
                artifacts.create(
                    self.vol_info.virtual_size, vol_format,
                    self.vol_info.disk_type, self.vol_info.description,
                    self.vol_info.parent_vol_id)
                artifacts.commit()


# TODO: Adopt the properties framework for managing complex verb parameters


class CreateVolumeInfo(object):
    def __init__(self, params):
        self.sd_id = _required(params, 'sd_id')
        self.img_id = _required(params, 'img_id')
        self.vol_id = _required(params, 'vol_id')
        self.virtual_size = _required(params, 'virtual_size')
        vol_types = [volume.VOLUME_TYPES[vt]
                     for vt in (volume.RAW_FORMAT, volume.COW_FORMAT)]
        self.vol_format = _enum(params, 'vol_format', vol_types)
        self.disk_type = _enum(params, 'disk_type', image.DISK_TYPES.values())
        self.description = params.get('description', '')
        self.parent_img_id = params.get('parent_img_id', volume.BLANK_UUID)
        self.parent_vol_id = params.get('parent_vol_id', volume.BLANK_UUID)
        self.initial_size = params.get('initial_size', 0)


def _required(params, name):
    if name not in params:
        raise exception.MissingParameter()
    return params[name]


def _enum(params, name, values):
    value = _required(params, name)
    if value not in values:
        raise se.InvalidParameterException(name, value)
    return value
