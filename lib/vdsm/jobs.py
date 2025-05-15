# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import logging
import threading

from vdsm.common import exception
from vdsm.common import response
from vdsm.config import config


_lock = threading.Lock()
_jobs = {}
_scheduler = None
# Message notification service
_notifier = None


class STATUS:
    PENDING = 'pending'    # Job has not started yet
    RUNNING = 'running'    # Job is running
    DONE = 'done'          # Job has finished successfully
    ABORTING = 'aborting'  # Job is running but abort is in progress
    ABORTED = 'aborted'    # Job was aborted by user request
    FAILED = 'failed'      # Job has failed


class ClientError(Exception):
    ''' Base class for client error '''
    name = None


class JobExistsError(ClientError):
    ''' Job already exists in _jobs collection '''
    name = 'JobExistsError'


class NoSuchJob(ClientError):
    ''' Job does not exist in _jobs collection '''
    name = 'NoSuchJob'


class JobNotDone(ClientError):
    ''' Job still in progress '''
    name = 'JobNotDone'


class JobNotActive(ClientError):
    ''' Job is not running or pending '''
    name = 'JobNotActive'


class AbortNotSupported(ClientError):
    ''' This type of job does not support aborting '''
    name = 'AbortNotSupported'


class Job(object):
    _JOB_TYPE = None

    # If set to True, jobs of this class will be automatically deleted when
    # aborted or finished after a configurable delay.
    autodelete = False

    def __init__(self, job_id, description=''):
        self._id = job_id
        self._status = STATUS.PENDING
        self._description = description
        self._status_lock = threading.Lock()
        self._error = None

    @property
    def id(self):
        return self._id

    @property
    def status(self):
        return self._status

    @property
    def description(self):
        return self._description

    @property
    def progress(self):
        return None

    @property
    def job_type(self):
        return self._JOB_TYPE

    @property
    def error(self):
        return self._error

    def info(self):
        ret = {'id': self.id,
               'status': self.status,
               'description': self.description,
               'job_type': self.job_type}

        if self.progress is not None:
            ret['progress'] = self.progress

        if self.error:
            ret['error'] = self.error.info()

        return ret

    @property
    def active(self):
        return self.status in (STATUS.PENDING, STATUS.RUNNING)

    def abort(self):
        with self._status_lock:
            if self.status == STATUS.PENDING:
                # Autodelete should only be handled here for pending state.
                # There is no operation running so we can go straight to
                # aborted state.  In all other cases, autodelete is handled as
                # the _run method finishes.
                self._status = STATUS.ABORTED
                logging.info("Aborted pending job %r.", self.id)
                self._autodelete_if_required()
                self._send_event()
            elif self.status == STATUS.RUNNING:
                self._status = STATUS.ABORTING
                logging.info("Aborting job %r...", self.id)
                self._abort()
            elif self.status == STATUS.ABORTING:
                logging.info("Retrying abort job %r...", self.id)
                self._abort()
            else:
                raise JobNotActive()

    def run(self):
        if not self._may_run():
            return
        try:
            self._run()
        except exception.ActionStopped:
            self._abort_completed()
        except Exception as e:
            self._run_failed(e)
        else:
            self._run_completed()
        finally:
            self._autodelete_if_required()
            self._send_event()

    def _may_run(self):
        """
        Check if the job should enter the running state.  Allowed origin states
        are aborted and pending.  If a pending job had been aborted we quietly
        refuse to run it.  The common case is to move a job from pending to
        running.
        """
        with self._status_lock:
            if self.status == STATUS.ABORTED:
                logging.debug('Refusing to run aborted job %r', self._id)
                return False
            if self.status != STATUS.PENDING:
                raise RuntimeError('Attempted to run job %r from state %r' %
                                   (self._id, self.status))
            self._status = STATUS.RUNNING
            logging.info("Running job %r...", self.id)
            return True

    def _abort_completed(self):
        """
        The job's _run method raised ActionStopped which indicates that all
        steps required to abort the job have been completed.  We move the job
        from the intermediate aborting state to the final aborted state.
        """
        with self._status_lock:
            if self.status == STATUS.ABORTING:
                logging.info("Abort completed for job %r", self.id)
            else:
                logging.warning("Unexpected ActionStopped exception in "
                                "job %r with status %r",
                                self.id, self.status)
            self._status = STATUS.ABORTED

    def _run_completed(self):
        """
        The job's _run method finished successfully.  Update state to done.
        """
        with self._status_lock:
            self._status = STATUS.DONE
            logging.info("Job %r completed", self.id)

    def _run_failed(self, e):
        """
        The job's _run method failed and raised an exception.  If we were in
        the process of aborting we consider the abort operation finished.
        Otherwise, move the job to failed state.
        """
        with self._status_lock:
            if self.status == STATUS.ABORTING:
                self._status = STATUS.ABORTED
                logging.exception("Exception while aborting job %r", self.id)
            else:
                self._status = STATUS.FAILED
                logging.exception("Job %r failed", self.id)
            if not isinstance(e, exception.VdsmException):
                e = exception.GeneralException(str(e))
            self._error = e

    def _abort(self):
        """
        May be implemented by child class.  This is an asynchronous operation
        which should trigger the abort quickly and return.  A successful return
        does not mean the job has stopped.  The caller must wait for the job
        status to change to aborted.
        - Should raise if the job could not be aborted
        - A successful abort must cause the job's _run method to raise an
          ActionStopped exception.
        """
        raise AbortNotSupported()

    def _run(self):
        """
        Must be implemented by child class
        """
        raise NotImplementedError()

    def _autodelete_if_required(self):
        if self.autodelete:
            timeout = config.getint("jobs", "autodelete_delay")
            if timeout >= 0:
                logging.info("Job %r will be deleted in %d seconds",
                             self.id, timeout)
                _scheduler.schedule(timeout, self._delete)

    def _delete(self):
        logging.info("Autodeleting job %r", self.info())
        try:
            _delete(self._id)
        except Exception:
            logging.exception("Cannot delete job %s", self._id)

    def _send_event(self):
        _notifier.notify('|jobs|status|%s' % self.id, params=self.info())

    def __repr__(self):
        s = "<{self.__class__.__name__} id={self.id} status={self.status} "
        if self.progress is not None:
            s += "progress={self.progress}% "
        s += "at 0x{id}>"
        return s.format(self=self, id=id(self))


def start(scheduler, notifier):
    global _scheduler
    global _notifier
    _scheduler = scheduler
    _notifier = notifier


def stop():
    pass


def delete(job_id):
    try:
        _delete(job_id)
    except ClientError as e:
        logging.info('Cannot delete job, error: %s', e)
        return response.error(e.name)
    return response.success()


def abort(job_id):
    try:
        job = get(job_id)
        job.abort()
    except ClientError as e:
        logging.info('Cannot abort job, error: %s', e)
        return response.error(e.name)
    return response.success()


def info(job_type=None, job_ids=()):
    job_ids = frozenset(job_ids)
    with _lock:
        jobs = list(_jobs.values())
    result = {}
    for job in jobs:
        if job_type and job.job_type != job_type:
            continue
        if job_ids and job.id not in job_ids:
            continue
        result[job.id] = job.info()
    return result


def add(job):
    with _lock:
        if job.id in _jobs:
            raise JobExistsError("Job %r exists" % job.id)
        _jobs[job.id] = job


def get(job_id):
    with _lock:
        if job_id not in _jobs:
            raise NoSuchJob("No such job %r" % job_id)
        return _jobs[job_id]


def _delete(job_id):
    with _lock:
        try:
            job = _jobs[job_id]
        except KeyError:
            raise NoSuchJob("No such job %r" % job_id)
        if job.active:
            raise JobNotDone("Job %r is %s" % (job_id, job.status))
        del _jobs[job_id]


# This should only be used by test code!
def _clear():
    with _lock:
        _jobs.clear()
