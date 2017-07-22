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

from vdsm.common import errors
from vdsm.storage import constants as sc
from vdsm.storage import guarded
from vdsm.storage import qemuimg
from vdsm.storage.sdc import sdCache
from vdsm.storage.sdm.volume_info import VolumeInfo

from . import base


class Error(errors.Base):
    msg = "Cannot amend volume {self.vol_id}: {self.reason}"

    def __init__(self, vol_id, reason):
        self.vol_id = vol_id
        self.reason = reason


class Job(base.Job):

    def __init__(self, job_id, host_id, vol_info, qcow2_attr):
        super(Job, self).__init__(job_id, 'amend_volume', host_id)
        # While performing operations, the volume is not set as illegal since
        # fail to amend a qcow volume should not reflect on the disk capability
        # to be used in a VM.
        #
        # qemu-img amend requires the entire chain
        # see https://bugzilla.redhat.com/1417460
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
