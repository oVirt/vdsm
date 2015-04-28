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
from vdsm import libvirtconnection
from vdsm import schedule
from vdsm.config import config
from vdsm.utils import monotonic_time

from . import sampling


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


def start(cif):
    global _operations

    _scheduler.start()
    _executor.start()

    def per_vm_operation(func, period):
        disp = VmDispatcher(
            cif.getVMs, _executor, func, _timeout_from(period))
        return Operation(disp, period)

    _operations = [
        # needs dispatching becuse updating the volume stats needs the
        # access the storage, thus can block.
        per_vm_operation(
            UpdateVolumes,
            config.getint('irs', 'vol_size_sample_interval')),

        # needs dispatching becuse access FS and libvirt data
        per_vm_operation(
            NumaInfoMonitor,
            config.getint('vars', 'vm_sample_numa_interval')),

        # Job monitoring need QEMU monitor access.
        per_vm_operation(
            BlockjobMonitor,
            config.getint('vars', 'vm_sample_jobs_interval')),

        # libvirt sampling using bulk stats can block, but unresponsive
        # domains are handled inside VMBulkSampler for performance reasons;
        # thus, does not need dispatching.
        Operation(
            sampling.VMBulkSampler(
                libvirtconnection.get(cif),
                cif.getVMs,
                sampling.stats_cache),
            config.getint('vars', 'vm_sample_interval')),

        # we do this only until we get high water mark notifications
        # from qemu. Access storage and/or qemu monitor, so can block,
        # thus we need dispatching.
        per_vm_operation(
            DriveWatermarkMonitor,
            config.getint('vars', 'vm_watermark_interval'))

    ]

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
            try:
                op = self._create(vm_obj)

                if not op.required:
                    continue
                # When dealing with blocked domains, we also want to avoid
                # to pile up jobs that libvirt can't handle and eventually
                # clog it.
                # We don't care too much about precise tracking, so it is
                # still OK if occasional misdetection occours, but we
                # definitely want to avoid known-bad situation and to
                # needlessly overload libvirt.
                if not op.runnable:
                    skipped.append(vm_id)
                    continue

            except Exception:
                # we want to make sure to have VM UUID logged
                self._log.exception("while dispatching %s to VM '%s'",
                                    self._create, vm_id)
            else:
                self._executor.dispatch(op, self._timeout)

        if skipped:
            self._log.warning('could not run %s on %s',
                              self._create, skipped)

    def __repr__(self):
        return 'VmDispatcher(%s)' % self._create


class UpdateVolumes(object):
    def __init__(self, vm):
        self._vm = vm

    @property
    def required(self):
        # Avoid queries from storage during recovery process
        return self._vm.isDisksStatsCollectionEnabled()

    @property
    def runnable(self):
        return self._vm.isDomainReadyForCommands()

    def __call__(self):
        for drive in self._vm.getDiskDevices():
            # TODO: If this block (it is actually possible?)
            # we must make sure we don't overwrite good data
            # with stale old data.
            self._vm.updateDriveVolume(drive)


class NumaInfoMonitor(object):
    def __init__(self, vm):
        self._vm = vm

    @property
    def required(self):
        return self._vm.hasGuestNumaNode

    @property
    def runnable(self):
        return self._vm.isDomainReadyForCommands()

    def __call__(self):
        self._vm.updateNumaInfo()


class BlockjobMonitor(object):
    def __init__(self, vm):
        self._vm = vm

    @property
    def required(self):
        # For performance reasons, we must avoid as much
        # as possible to create per-vm executor tasks, even
        # though they will do nothing but a few check and exit
        # early, as they do if a VM doesn't have Block Jobs to
        # monitor (most often true).
        return self._vm.hasVmJobs

    @property
    def runnable(self):
        return self._vm.isDomainReadyForCommands()

    def __call__(self):
        self._vm.updateVmJobs()


class DriveWatermarkMonitor(object):
    def __init__(self, vm):
        self._vm = vm

    @property
    def required(self):
        # Avoid queries from storage during recovery process
        return self._vm.isDisksStatsCollectionEnabled()

    @property
    def runnable(self):
        return self._vm.isDomainReadyForCommands()

    def __call__(self):
        self._vm.extendDrivesIfNeeded()
