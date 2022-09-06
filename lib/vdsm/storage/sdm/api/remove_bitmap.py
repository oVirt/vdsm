# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.common import errors
from vdsm.storage import bitmaps
from vdsm.storage import constants as sc
from vdsm.storage import guarded
from vdsm.storage.sdm.volume_info import VolumeInfo

from . import base


class Error(errors.Base):
    msg = ("Cannot remove bitmap {self.bitmap} from "
           "volume {self.vol_id}: {self.reason}")

    def __init__(self, vol_id, bitmap, reason):
        self.vol_id = vol_id
        self.reason = reason
        self.bitmap = bitmap


class Job(base.Job):

    def __init__(self, job_id, host_id, vol_info, bitmap):
        super(Job, self).__init__(job_id, 'remove_bitmap', host_id)
        self._vol_info = VolumeInfo(vol_info, host_id)
        self.bitmap = bitmap

    def _validate(self):
        if self._vol_info.volume.getFormat() != sc.COW_FORMAT:
            raise Error(
                self._vol_info.vol_id,
                self.bitmap,
                "volume is not in COW format")

    def _run(self):
        with guarded.context(self._vol_info.locks):
            self._validate()
            with self._vol_info.prepare():
                with self._vol_info.volume_operation():
                    bitmaps.remove_bitmap(self._vol_info.path, self.bitmap)
