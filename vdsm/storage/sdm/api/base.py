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

import logging

from vdsm import jobs
from vdsm import exception

from storage.threadLocal import vars


class Job(jobs.Job):
    _JOB_TYPE = "storage"
    log = logging.getLogger('storage.sdmjob')

    def __init__(self, job_id, desc, host_id):
        super(Job, self).__init__(job_id, desc)
        self._status = jobs.STATUS.PENDING
        self.host_id = host_id

    def run(self):
        self._status = jobs.STATUS.RUNNING
        vars.job_id = self.id
        try:
            self._run()
        except Exception as e:
            self.log.exception("Job (id=%s desc=%s) failed",
                               self.id, self.description)
            if not isinstance(e, exception.VdsmException):
                e = exception.GeneralException(str(e))
            self._error = e
            self._status = jobs.STATUS.FAILED
        else:
            self._status = jobs.STATUS.DONE
        finally:
            vars.job_id = None
