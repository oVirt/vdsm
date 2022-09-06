# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

from vdsm.storage import guarded
from vdsm.storage import validators

from .copy_data import CopyDataDivEndpoint
from . import base


class Job(base.Job):

    def __init__(self, job_id, host_id, vol_info, vol_attr):
        super(Job, self).__init__(job_id, 'update_volume', host_id)
        self._endpoint = CopyDataDivEndpoint(vol_info, host_id)
        self._vol_attr = validators.VolumeAttributes(vol_attr)

    def _run(self):
        with guarded.context(self._endpoint.locks):
            self._endpoint.volume.update_attributes(self._endpoint.generation,
                                                    self._vol_attr)
