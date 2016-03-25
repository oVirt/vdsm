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

import uuid

from testlib import VdsmTestCase
from sdmtestlib import wait_for_job

from vdsm import jobs
from vdsm import exception
from vdsm.storage.threadlocal import vars

from storage.sdm.api import base


class ApiBaseTests(VdsmTestCase):

    def run_job(self, job):
        self.assertEqual(jobs.STATUS.PENDING, job.status)
        self.assertIsNone(getattr(vars, 'job_id', None))
        job.run()
        wait_for_job(job)
        self.assertIsNone(getattr(vars, 'job_id', None))

    def test_states(self):
        job = TestingJob()
        self.run_job(job)
        self.assertEqual(jobs.STATUS.DONE, job.status)

    def test_default_exception(self):
        message = "testing failure"
        job = TestingJob(Exception(message))
        self.run_job(job)
        self.assertEqual(jobs.STATUS.FAILED, job.status)
        self.assertIsInstance(job.error, exception.GeneralException)
        self.assertIn(message, str(job.error))

    def test_vdsm_exception(self):
        job = TestingJob(exception.VdsmException())
        self.run_job(job)
        self.assertEqual(jobs.STATUS.FAILED, job.status)
        self.assertIsInstance(job.error, exception.VdsmException)


class TestingJob(base.Job):

    def __init__(self, exception=None):
        job_id = str(uuid.uuid4())
        super(TestingJob, self).__init__(job_id, 'testing_job', 'host_id')
        self.exception = exception

    def _run(self):
        assert(self.status == jobs.STATUS.RUNNING)
        assert(vars.job_id == self.id)
        if self.exception:
            raise self.exception
