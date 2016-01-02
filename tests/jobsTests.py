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

import uuid

from vdsm import jobs, response, utils

from testlib import VdsmTestCase, expandPermutations, permutations


class TestingJob(jobs.Job):
    _JOB_TYPE = 'testing'

    def __init__(self, status=jobs.STATUS.PENDING):
        jobs.Job.__init__(self, str(uuid.uuid4()))
        self._aborted = False
        self._status = status

    def _abort(self):
        self._aborted = True


class FooJob(TestingJob):
    _JOB_TYPE = 'foo'


class BarJob(TestingJob):
    _JOB_TYPE = 'bar'


class ProgressingJob(jobs.Job):

    def __init__(self):
        jobs.Job.__init__(self, str(uuid.uuid4()))
        self._progress = None

    @property
    def progress(self):
        return self._progress

    @progress.setter
    def progress(self, value):
        self._progress = value


@expandPermutations
class JobsTests(VdsmTestCase):
    TIMEOUT = 1

    def setUp(self):
        jobs._clear()

    def test_job_initial_state(self):
        job = TestingJob()
        self.assertEqual(jobs.STATUS.PENDING, job.status)
        self.assertEqual('', job.description)
        self.assertEqual('testing', job.job_type)

    def test_job_info(self):
        job = TestingJob()
        self.assertEqual({'id': job.id,
                          'status': jobs.STATUS.PENDING,
                          'job_type': 'testing',
                          'description': ''},
                         job.info())

    def test_add_job(self):
        job = TestingJob()
        jobs.add(job)
        self.assertEqual(1, len(jobs._jobs))

    def test_add_existing_job(self):
        job = TestingJob()
        jobs.add(job)
        self.assertRaises(jobs.JobExistsError, jobs.add, job)

    def test_get_job(self):
        job = TestingJob()
        jobs.add(job)
        self.assertEqual(job.id, jobs.get(job.id).id)

    def test_get_unknown_job(self):
        self.assertRaises(jobs.NoSuchJob, jobs.get, 'foo')

    def test_get_jobs_info_empty(self):
        self.assertEqual({}, jobs.info())

    def test_get_jobs_info_any(self):
        foo = FooJob()
        jobs.add(foo)
        bar = BarJob()
        jobs.add(bar)
        self.assertEqual({foo.id: foo.info(), bar.id: bar.info()},
                         jobs.info())

    def test_get_jobs_info_by_type(self):
        foo = FooJob()
        jobs.add(foo)
        bar = BarJob()
        jobs.add(bar)
        self.assertEqual({bar.id: bar.info()},
                         jobs.info(job_type=bar.job_type))

    def test_get_jobs_info_by_uuid_single(self):
        foo = FooJob()
        jobs.add(foo)
        bar = BarJob()
        jobs.add(bar)
        self.assertEqual({foo.id: foo.info()},
                         jobs.info(job_ids=[foo.id]))

    def test_get_jobs_info_by_uuid_multi(self):
        foo = FooJob()
        jobs.add(foo)
        bar = BarJob()
        jobs.add(bar)
        self.assertEqual({foo.id: foo.info(), bar.id: bar.info()},
                         jobs.info(job_ids=[foo.id, bar.id]))

    def test_get_jobs_info_by_type_and_uuid(self):
        foo = FooJob()
        jobs.add(foo)
        bar = BarJob()
        jobs.add(bar)
        self.assertEqual({}, jobs.info(job_type=bar.job_type,
                                       job_ids=[foo.id]))

    def test_abort_job(self):
        job = TestingJob()
        jobs.add(job)
        jobs.abort(job.id)
        self.assertEqual(jobs.STATUS.ABORTED, job.status)
        self.assertTrue(job._aborted)

    def test_abort_unknown_job(self):
        self.assertEqual(response.error(jobs.NoSuchJob.name),
                         jobs.abort('foo'))

    def test_abort_not_supported(self):
        job = jobs.Job(str(uuid.uuid4()))
        jobs.add(job)
        self.assertEqual(response.error(jobs.AbortNotSupported.name),
                         jobs.abort(job.id))

    @permutations([
        [jobs.STATUS.PENDING, True],
        [jobs.STATUS.RUNNING, True],
        [jobs.STATUS.ABORTED, False],
        [jobs.STATUS.DONE, False],
        [jobs.STATUS.FAILED, False]
    ])
    def test_job_active(self, status, active):
        job = TestingJob(status)
        self.assertEqual(active, job.active)

    @permutations([
        [jobs.STATUS.ABORTED],
        [jobs.STATUS.DONE],
        [jobs.STATUS.FAILED]
    ])
    def test_delete_inactive_job(self, status):
        job = TestingJob(status)
        jobs.add(job)
        self.assertEqual(response.success(), jobs.delete(job.id))

    @permutations([
        [jobs.STATUS.PENDING],
        [jobs.STATUS.RUNNING],
    ])
    def test_delete_active_job(self, status):
        job = TestingJob(status)
        jobs.add(job)
        self.assertEqual(response.error(jobs.JobNotDone.name),
                         jobs.delete(job.id))

    def test_delete_unknown_job(self):
        self.assertEqual(response.error(jobs.NoSuchJob.name),
                         jobs.delete('foo'))

    def test_job_get_progress(self):
        job = ProgressingJob()

        # Job queued or initializing, no progress yet
        self.assertNotIn('progress', job.info())

        # Job running
        for i in [0, 42, 100]:
            job.progress = i
            self.assertEqual(i, job.info()['progress'])

    def test_job_get_error(self):
        job = TestingJob()
        self.assertIsNone(job.error)
        self.assertNotIn('error', job.info())

        error = utils.GeneralException()
        job._error = error
        self.assertEqual(job.error, error)
        self.assertEqual(error.info(), job.info()['error'])

    def test_job_repr(self):
        job = TestingJob()
        rep = repr(job)
        self.assertIn("TestingJob", rep)
        self.assertIn("id=%s" % job.id, rep)
        self.assertIn("status=pending", rep)
        self.assertNotIn("progress=", rep)

    def test_job_repr_with_progress(self):
        job = ProgressingJob()
        job.progress = 32
        rep = repr(job)
        self.assertIn("progress=32%", rep)
