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
