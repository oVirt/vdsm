#
# Copyright 2016 Red Hat, Inc.
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

from contextlib import contextmanager

from vdsm import qemuimg
from vdsm import properties
from vdsm.storage import constants as sc
from vdsm.storage import guarded

from storage import resourceManager as rm
from storage import sd
from storage import volume
from storage.sdc import sdCache

from . import base


class Error(Exception):
    msg = "Cannot amend volume {self.vol_id}: {self.reason}"

    def __init__(self, vol_id, reason):
        self.vol_id = vol_id
        self.reason = reason

    def __str__(self):
        return self.msg.format(self=self)


class Job(base.Job):

    def __init__(self, job_id, host_id, vol_info, qcow2_attr):
        super(Job, self).__init__(job_id, 'amend_volume', host_id)
        self._vol_info = VolumeInfo(vol_info, host_id)

        # Add validation in a new class for volume attribute
        # We currently can't use the validation properties.enum
        # since it doesn't support optional enum.
        self._qcow2_attr = Qcow2Attributes(qcow2_attr)

    def _validate(self):
        if self._vol_info.volume.getFormat() != sc.COW_FORMAT:
            raise Error(self._vol_info.vol_id, "volume is not COW format")
        if self._vol_info.volume.isShared():
            raise Error(self._vol_info.vol_id, "volume is shared")
        sd = sdCache.produce_manifest(self._vol_info.sd_id)
        if not sd.supports_qcow2_compat(self._qcow2_attr.compat):
            raise Error(self._vol_info.vol_id,
                        "storage domain %s does not support compat %s" %
                        (self._vol_info.sd_id, self._qcow2_attr.compat))

    def _run(self):
        with guarded.context(self._vol_info.locks):
            self._validate()
            with self._vol_info.prepare():
                with self._vol_info.volume_operation():
                    qemuimg.amend(self._vol_info.path, self._qcow2_attr.compat)


class Qcow2Attributes(object):

    def __init__(self, params):
        compat = params.get("compat")
        if compat is None:
            raise ValueError("No attributes to amend")
        if not qemuimg.supports_compat(compat):
            raise ValueError("Unsupported qcow2 compat %s" % compat)
        self.compat = compat


class VolumeInfo(properties.Owner):
    """
    VolumInfo should be used for performing qemu metadata operations
    on any volume in a chain except shared volume.
    A volume is prepared in read-write mode.
    While performing operations, the volume is not set as illegal since
    fail to amend a qcow volume should not reflect on the disk capability
    to be used in a VM.
    """
    sd_id = properties.UUID(required=True)
    img_id = properties.UUID(required=True)
    vol_id = properties.UUID(required=True)
    generation = properties.Integer(required=True, minval=0,
                                    maxval=sc.MAX_GENERATION)

    def __init__(self, params, host_id):
        self.sd_id = params.get('sd_id')
        self.img_id = params.get('img_id')
        self.vol_id = params.get('vol_id')
        self.generation = params.get('generation')
        self._host_id = host_id
        self._vol = None

    @property
    def locks(self):
        img_ns = sd.getNamespace(sc.IMAGE_NAMESPACE, self.sd_id)
        return [
            rm.ResourceManagerLock(sc.STORAGE, self.sd_id, rm.SHARED),
            rm.ResourceManagerLock(img_ns, self.img_id, rm.EXCLUSIVE),
            volume.VolumeLease(self._host_id, self.sd_id, self.img_id,
                               self.vol_id)
        ]

    @property
    def path(self):
        return self.volume.getVolumePath()

    @property
    def volume(self):
        if self._vol is None:
            dom = sdCache.produce_manifest(self.sd_id)
            self._vol = dom.produceVolume(self.img_id, self.vol_id)
        return self._vol

    def volume_operation(self):
        return self.volume.operation(requested_gen=self.generation,
                                     set_illegal=False)

    @contextmanager
    def prepare(self):
        self.volume.prepare(rw=True, justme=True)
        try:
            yield
        finally:
            self.volume.teardown(self.sd_id, self.vol_id, justme=True)
