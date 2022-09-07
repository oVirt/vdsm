# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import threading
import uuid

from vdsm.common import exception, response
from vdsm import jobs

from fakelib import FakeNotifier
from fakelib import FakeScheduler
from monkeypatch import MonkeyPatchScope
from testlib import VdsmTestCase, expandPermutations, permutations
from testlib import make_config
from testlib import start_thread


class TestingJob(jobs.Job):
    _JOB_TYPE = 'testing'

    def __init__(self, status=jobs.STATUS.PENDING, exception=None):
        jobs.Job.__init__(self, str(uuid.uuid4()))
        self._aborted = False
        self._status = status
        self._exception = exception

    def _abort(self):
        self._aborted = True

    def _run(self):
        assert(self.status == jobs.STATUS.RUNNING)
        if self._exception:
            raise self._exception


class FooJob(TestingJob):
    _JOB_TYPE = 'foo'


class BarJob(TestingJob):
    _JOB_TYPE = 'bar'


class AutodeleteJob(TestingJob):
    autodelete = True


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


class StuckJob(TestingJob):

    def __init__(self):
        TestingJob.__init__(self)
        self.event_running = threading.Event()
        self.event_aborted = threading.Event()

    def _run(self):
        self.event_running.set()
        self.event_aborted.wait(1)
        raise exception.ActionStopped()

    def _abort(self):
        self.event_aborted.set()


@expandPermutations
class JobsTests(VdsmTestCase):
    TIMEOUT = 1

    def setUp(self):
        self.scheduler = FakeScheduler()
        self.notifier = FakeNotifier()
        jobs.start(self.scheduler, self.notifier)

    def tearDown(self):
        jobs._clear()

    def run_job(self, job):
        self.assertEqual(jobs.STATUS.PENDING, job.status)
        job.run()

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

    @permutations([
        [jobs.STATUS.PENDING, jobs.STATUS.ABORTED, False],
        [jobs.STATUS.RUNNING, jobs.STATUS.ABORTING, True],
    ])
    def test_abort_job(self, status, aborted_status, did_abort):
        job = TestingJob(status)
        jobs.add(job)
        jobs.abort(job.id)
        self.assertEqual(aborted_status, job.status)
        self.assertEqual(did_abort, job._aborted)

    def test_abort_running_job(self):
        job = StuckJob()
        jobs.add(job)
        t = start_thread(job.run)
        job.event_running.wait(1)
        self.assertEqual(jobs.STATUS.RUNNING, job.status)
        jobs.abort(job.id)
        t.join()
        self.assertEqual(jobs.STATUS.ABORTED, job.status)
        self.validate_event_sent(job)

    @permutations([
        [jobs.STATUS.ABORTED, jobs.JobNotActive.name],
        [jobs.STATUS.DONE, jobs.JobNotActive.name],
        [jobs.STATUS.FAILED, jobs.JobNotActive.name]
    ])
    def test_abort_from_invalid_state(self, status, err):
        job = TestingJob(status)
        jobs.add(job)
        res = jobs.abort(job.id)
        self.assertEqual(response.error(err), res)

    def test_abort_unknown_job(self):
        self.assertEqual(response.error(jobs.NoSuchJob.name),
                         jobs.abort('foo'))

    def test_abort_not_supported(self):
        job = jobs.Job(str(uuid.uuid4()))
        job._status = jobs.STATUS.RUNNING
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

        error = exception.GeneralException()
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

    def test_running_states(self):
        job = TestingJob()
        self.run_job(job)
        self.assertEqual(jobs.STATUS.DONE, job.status)
        self.validate_event_sent(job)

    @permutations([
        [jobs.STATUS.RUNNING],
        [jobs.STATUS.DONE],
        [jobs.STATUS.FAILED]
    ])
    def test_run_from_invalid_state(self, state):
        job = TestingJob(state)
        self.assertRaises(RuntimeError, job.run)
        self.validate_event_not_sent()

    def test_default_exception(self):
        message = "testing failure"
        job = TestingJob(exception=Exception(message))
        self.run_job(job)
        self.assertEqual(jobs.STATUS.FAILED, job.status)
        self.assertIsInstance(job.error, exception.GeneralException)
        self.assertIn(message, str(job.error))
        self.validate_event_sent(job)

    def test_vdsm_exception(self):
        job = TestingJob(exception=exception.VdsmException())
        self.run_job(job)
        self.assertEqual(jobs.STATUS.FAILED, job.status)
        self.assertIsInstance(job.error, exception.VdsmException)
        self.validate_event_sent(job)

    def _verify_autodelete(self, job, expected_delay):
        self.assertEqual(1, len(self.scheduler.calls))
        delay, callable = self.scheduler.calls[0]
        self.assertEqual(expected_delay, delay)
        self.assertIn(job.id, jobs.info())
        callable()
        self.assertNotIn(job.id, jobs.info())

    @permutations(((None, ), (Exception(),)))
    def test_autodelete_when_finished(self, error):
        cfg = make_config([('jobs', 'autodelete_delay', '10')])
        job = AutodeleteJob(exception=error)
        jobs.add(job)
        with MonkeyPatchScope([(jobs, 'config', cfg)]):
            self.run_job(job)
            self._verify_autodelete(job, 10)

    def test_autodelete_when_aborted(self):
        cfg = make_config([('jobs', 'autodelete_delay', '10')])
        job = AutodeleteJob()
        jobs.add(job)
        with MonkeyPatchScope([(jobs, 'config', cfg)]):
            job.abort()
            self._verify_autodelete(job, 10)

    def test_autodelete_disabled(self):
        cfg = make_config([('jobs', 'autodelete_delay', '-1')])
        job = AutodeleteJob()
        jobs.add(job)
        with MonkeyPatchScope([(jobs, 'config', cfg)]):
            self.run_job(job)
            self.assertEqual(0, len(self.scheduler.calls))

    def validate_event_sent(self, job):
        expected_calls = [('|jobs|status|%s' % job.id, job.info())]
        self.assertEqual(self.notifier.calls, expected_calls)

    def validate_event_not_sent(self):
        self.assertEqual(self.notifier.calls, [])
