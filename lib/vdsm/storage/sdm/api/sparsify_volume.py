# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
