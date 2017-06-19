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

from vdsm.common import exception
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import resourceManager as rm

from storage import image

from . import base


class Job(base.Job):
    def __init__(self, job_id, host_id, sd_manifest, vol_info):
        super(Job, self).__init__(job_id, 'create_volume', host_id)
        self.sd_manifest = sd_manifest
        self.vol_info = vol_info

    def _run(self):
        vol_format = sc.name2type(self.vol_info.vol_format)

        with self.sd_manifest.domain_lock(self.host_id):
            image_res_ns = rm.getNamespace(sc.IMAGE_NAMESPACE,
                                           self.sd_manifest.sdUUID)
            with rm.acquireResource(image_res_ns, self.vol_info.img_id,
                                    rm.EXCLUSIVE):
                artifacts = self.sd_manifest.get_volume_artifacts(
                    self.vol_info.img_id, self.vol_info.vol_id)
                artifacts.create(
                    self.vol_info.virtual_size, vol_format,
                    self.vol_info.disk_type, self.vol_info.description,
                    self.vol_info.parent, self.vol_info.initial_size)
                artifacts.commit()


# TODO: Adopt the properties framework for managing complex verb parameters


class CreateVolumeInfo(object):
    def __init__(self, params):
        self.sd_id = _required(params, 'sd_id')
        self.img_id = _required(params, 'img_id')
        self.vol_id = _required(params, 'vol_id')
        self.virtual_size = _required(params, 'virtual_size')
        vol_types = [sc.VOLUME_TYPES[vt]
                     for vt in (sc.RAW_FORMAT, sc.COW_FORMAT)]
        self.vol_format = _enum(params, 'vol_format', vol_types)
        self.disk_type = _enum(params, 'disk_type', image.DISK_TYPES.values())
        self.description = params.get('description', '')
        parent = params.get('parent', None)
        self.parent = None if parent is None else ParentVolumeInfo(parent)
        self.initial_size = params.get('initial_size')


class ParentVolumeInfo(object):
    def __init__(self, params):
        self.img_id = _required(params, 'img_id')
        self.vol_id = _required(params, 'vol_id')


def _required(params, name):
    if name not in params:
        raise exception.MissingParameter()
    return params[name]


def _enum(params, name, values):
    value = _required(params, name)
    if value not in values:
        raise se.InvalidParameterException(name, value)
    return value
