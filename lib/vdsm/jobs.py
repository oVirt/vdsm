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

import logging
import threading

from vdsm import response


_lock = threading.Lock()
_jobs = {}


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
        raise NotImplementedError()

    @property
    def job_type(self):
        return self._JOB_TYPE

    @property
    def error(self):
        return self._error

    def info(self):
        ret = {'status': self.status,
               'description': self.description,
               'progress': self.progress,
               'job_type': self.job_type}

        if self.error:
            ret['error'] = self.error.response()

        return ret

    def abort(self):
        self._status = STATUS.ABORTED
        logging.info('Job %r aborting...', self._id)
        self._abort()

    def validate_done(self):
        if self.status != STATUS.DONE:
            raise JobNotDone("Job %r is %s" % (self.id, self.status))

    def validate_not_active(self):
        if self.status not in (STATUS.DONE, STATUS.ABORTED, STATUS.FAILED):
            raise JobNotDone("Job %r is %s" % (self.id, self.status))

    def _abort(self):
        """
        May be implemented by child class
        """
        raise AbortNotSupported()


# This helper should only be called by test code.  Everything else should be
# using the public APIs.
def _clear():
    with _lock:
        _jobs.clear()


def delete(job_id):
    try:
        job = get(job_id)
        job.validate_not_active()
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


def info(type_filter=None):
    with _lock:
        jobs = _jobs.values()
    return {job.id: job.info() for job in jobs
            if not type_filter or job.job_type == type_filter}


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
        if job_id not in _jobs:
            raise NoSuchJob("No such job %r" % job_id)
        del _jobs[job_id]
