# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.storage import bitmaps
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage.sdm.volume_info import VolumeInfo

from . import base


class Job(base.Job):

    def __init__(self, job_id, host_id, vol_info):
        super(Job, self).__init__(job_id, 'list_bitmaps', host_id)
        self._vol_info = VolumeInfo(vol_info, host_id)
        self._bitmaps = None

    def _validate(self):
        if self._vol_info.volume.getFormat() != sc.COW_FORMAT:
            raise se.UnsupportedOperation(
                "Volume is not in COW format",
                vol_uuid=self._vol_info.vol_id)

    def _run(self):
        with guarded.context(self._vol_info.locks):
            self._validate()
            with self._vol_info.prepare():
                with self._vol_info.volume_operation():
                    self._bitmaps = bitmaps.list_bitmaps(self._vol_info.path)

    @property
    def bitmaps(self):
        return self._bitmaps
