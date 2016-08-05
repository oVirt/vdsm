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
import threading

from vdsm import exception
from vdsm import response
from vdsm.config import config


_lock = threading.Lock()
_jobs = {}
_scheduler = None


class STATUS:
    PENDING = 'pending'  # Job has not started yet
    RUNNING = 'running'  # Job is running
    DONE = 'done'        # Job has finished successfully
    ABORTED = 'aborted'  # Job was aborted by user request
    FAILED = 'failed'    # Job has failed


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
        # TODO: Don't abort if not pending and not running
        logging.info('Job %r aborting...', self._id)
        self._abort()
        self._status = STATUS.ABORTED
        # We MUST NOT autodelete a job if abort failed.  Otherwise there could
        # still be ongoing operations on storage without any associated job.
        if self.autodelete:
            self._autodelete()

    def run(self):
        # TODO: Don't run if aborted or not pending
        self._status = STATUS.RUNNING
        try:
            self._run()
        except Exception as e:
            logging.exception("Job (id=%s desc=%s) failed",
                              self.id, self.description)
            if not isinstance(e, exception.VdsmException):
                e = exception.GeneralException(str(e))
            self._error = e
            self._status = STATUS.FAILED
        else:
            self._status = STATUS.DONE
        finally:
            if self.autodelete:
                self._autodelete()

    def _abort(self):
        """
        May be implemented by child class
        - Must raise if the job could not be aborted
        - Must not raise if the job was aborted
        """
        raise AbortNotSupported()

    def _run(self):
        """
        Must be implemented by child class
        """
        raise NotImplementedError()

    def _autodelete(self):
        timeout = config.getint("jobs", "autodelete_delay")
        if timeout >= 0:
            _scheduler.schedule(timeout, self._delete)

    def _delete(self):
        logging.info("Autodeleting job %r", self.info())
        try:
            _delete(self._id)
        except Exception:
            logging.exception("Cannot delete job %s", self._id)

    def __repr__(self):
        s = "<{self.__class__.__name__} id={self.id} status={self.status} "
        if self.progress is not None:
            s += "progress={self.progress}% "
        s += "at 0x{id}>"
        return s.format(self=self, id=id(self))


def start(scheduler):
    global _scheduler
    _scheduler = scheduler


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
        jobs = _jobs.values()
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
