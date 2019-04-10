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

from vdsm import virtsparsify
from vdsm.common.exception import VdsmException
from vdsm.storage import guarded
from vdsm.storage.sdm.volume_info import VolumeInfo

from . import base


class SparsifyException(VdsmException):
    ''' Base class for sparsify exceptions '''


class VolumeIsNotLeafException(SparsifyException):
    msg = 'Volume to be sparsified is not a leaf volume'


class Job(base.Job):

    def __init__(self, job_id, host_id, vol_info):
        super(Job, self).__init__(job_id, 'sparsify_volume', host_id)
        self._vol_info = VolumeInfo(vol_info, host_id)

    def _validate(self):
        if not self._vol_info.volume.isLeaf():
            raise VolumeIsNotLeafException()
        # Not checking if the volume is a template, because there is no
        # easy way to verify this on VDSM side. Relying on Engine to assure
        # this constraint.

    def _run(self):
        with guarded.context(self._vol_info.locks):
            self._validate()
            with self._vol_info.prepare():
                with self._vol_info.volume_operation():
                    virtsparsify.sparsify_inplace(self._vol_info.path)
