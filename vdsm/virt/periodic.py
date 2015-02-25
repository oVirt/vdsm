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

"""
code to perform periodic maintenance and bookkeeping of the VMs.
"""

import logging
import threading

from vdsm import executor
from vdsm import schedule
from vdsm.config import config
from vdsm.utils import monotonic_time


# just a made up number. Maybe should be equal to number of cores?
# TODO: make them tunable through private, unsupported configuration items
_WORKERS = config.getint('sampling', 'periodic_workers')
_TASK_PER_WORKER = config.getint('sampling', 'periodic_task_per_worker')
_TASKS = _WORKERS * _TASK_PER_WORKER


_scheduler = schedule.Scheduler(name="periodic.Scheduler",
                                clock=monotonic_time)

_executor = executor.Executor(name="periodic.Executor",
                              workers_count=_WORKERS,
                              max_tasks=_TASKS,
                              scheduler=_scheduler)
_operations = []


def _timeout_from(interval):
    """
    Estimate a sensible timeout given a periodic interval.
    """
    return interval / 2.


def _dispatched_operation(get_vms, func, period):
    disp = VmDispatcher(get_vms, _executor, func, _timeout_from(period))
    return Operation(disp, period)


def start(cif):
    # `cif' will be used by future patches, for getVMs.

    _scheduler.start()
    _executor.start()

    for op in _operations:
        op.start()


def stop():
    for op in _operations:
        op.stop()

    _executor.stop(wait=False)
    _scheduler.stop(wait=False)


class Operation(object):
    """
    Operation runs a callable with a given period until
    someone stops it.
    Operations builds on Schedule and on Executor,
    so that the underlying "func" is called periodically.
    It would be called again even if a former call is blocked.
    """

    _log = logging.getLogger("periodic.Operation")

    def __init__(self, func, period, timeout=0,
                 scheduler=None, executor=None):
        """
        parameters:

        func: callable, without arguments (task interface).
        period: `func' will be invoked every `period' seconds.
                Please note that timing may not be exact due to
                (OS) scheduling constraings.
        timeout: same meaning of Executor.dispatch
        scheduler: Scheduler instance to use
        executor: Executor instance to use
        """
        self._func = func
        self._period = period
        self._timeout = _timeout_from(period) if timeout == 0 else timeout
        self._scheduler = _scheduler if scheduler is None else scheduler
        self._executor = _executor if executor is None else executor
        self._lock = threading.Lock()
        self._running = False
        self._call = None

    def start(self):
        with self._lock:
            if self._running:
                raise AssertionError("Operation already running")
            self._log.debug("starting operation %s", self._func)
            self._running = True
            # we do _dispatch instead of _step here to have some
            # data as soon as possibile
            self._dispatch()

    def stop(self):
        with self._lock:
            if self._running:
                self._log.debug("stopping operation %s", self._func)
                self._running = False
                if self._call:
                    self._call.cancel()
                    self._call = None

    def __call__(self):
        try:
            self._func()
        except Exception:
            self._log.exception("%s operation failed", self._func)

    def _step(self):
        """
        Schedule a next call of `func'
        """
        self._log.debug("after %f seconds: %s", self._period, self._func)
        self._call = self._scheduler.schedule(self._period,
                                              self._try_to_dispatch)

    def _try_to_dispatch(self):
        """
        Dispatch anoter Execution, if Operation is still running.
        """
        with self._lock:
            if self._running:
                self._dispatch()

    def _dispatch(self):
        """
        Send `func' to Executor to be run as soon as possible.
        """
        self._call = None
        self._executor.dispatch(self, self._timeout)
        self._step()


class VmDispatcher(object):
    """
    Adapter class. Dispatch an Operation to all VMs, to improve
    isolation among them.
    """

    _log = logging.getLogger("periodic.VmDispatcher")

    def __init__(self, get_vms, executor, create, timeout):
        """
        get_vms: callable which will return a dict which maps
                 vm_ids to vm_instances
        executor: executor.Executor instance
        create: callable to obtain the real callable to
                dispatch, with its timeout
        """
        self._get_vms = get_vms
        self._executor = executor
        self._create = create
        self._timeout = timeout

    def __call__(self):
        vms = self._get_vms()
        skipped = []

        for vm_id, vm_obj in vms.iteritems():
            op = self._create(vm_obj)

            if not op.required:
                continue

            # When dealing with blocked domains, we also want to avoid
            # to pile up jobs that libvirt can't handle and eventually clog it.
            # We don't care too much about precise tracking, so it is still OK
            # if occasional misdetection occours, but we definitely want to
            # avoid known-bad situation and to needlessly overload libvirt.
            if op.runnable:
                self._executor.dispatch(op, self._timeout)
            else:
                skipped.append(vm_id)

        if skipped:
            self._log.warning('could not run %s on %s',
                              self._create, skipped)

    def __repr__(self):
        return 'VmDispatcher(%s)' % self._create
