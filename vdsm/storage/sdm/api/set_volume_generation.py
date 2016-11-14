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

from . import base
from storage.sdm.api.copy_data import CopyDataDivEndpoint


class Job(base.Job):
    """
    Change a volume's generation ID from a known value to a new value.
    """

    def __init__(self, job_id, host_id, vol_info, new_gen):
        super(Job, self).__init__(job_id, 'set_volume_generation', host_id)
        self._endpoint = CopyDataDivEndpoint(vol_info, host_id, writable=True)
        self._new_gen = new_gen

    def _run(self):
        with guarded.context(self._endpoint.locks):
            self._endpoint.volume.set_generation(self._endpoint.generation,
                                                 self._new_gen)
