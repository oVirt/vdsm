#
# Copyright 2016-2021 Red Hat, Inc.
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
from __future__ import division

"""
Code to perform periodic maintenance and bookkeeping of the VMs.
"""

import logging
import threading

import libvirt
import six

from vdsm import executor
from vdsm import host
from vdsm import throttledlog
from vdsm.common import errors
from vdsm.common import exception
from vdsm.common import libvirtconnection
from vdsm.config import config
from vdsm.virt import migration
from vdsm.virt import recovery
from vdsm.virt import sampling
from vdsm.virt import virdomain
from vdsm.virt import vmstatus
from vdsm.virt.externaldata import ExternalDataKind
from vdsm.virt.utils import vm_kill_paused_timeout


# Just a made up number. Maybe should be equal to number of cores?
# TODO: make them tunable through private, unsupported configuration items
_WORKERS = config.getint('sampling', 'periodic_workers')
_TASK_PER_WORKER = config.getint('sampling', 'periodic_task_per_worker')
_TASKS = _WORKERS * _TASK_PER_WORKER
_MAX_WORKERS = config.getint('sampling', 'max_workers')
_THROTTLING_INTERVAL = 10  # seconds

_operations = []
_executor = None


class Error(errors.Base):
    msg = 'Generic error for periodic infrastructure'


class InvalidValue(Error):
    msg = 'Invalid {self.value} for {self.key} for Operation {self.op_desc}'

    def __init__(self, op_desc, key, value):
        self.op_desc = op_desc
        self.key = key
        self.value = value


def _timeout_from(interval):
    """
    Estimate a sensible timeout given a periodic interval.
    """
    return interval / 2.


def start(cif, scheduler):
    """
    Starts all the periodic Operations, to be run in one executor.Executor
    instance owned by the `periodic` module.
    There is no guarantee on the order on which the operations will be
    started; this function only guarantees that it will attempt to
    start every known Operation.
    """
    global _executor
    global _operations

    _executor = executor.Executor(name="periodic",
                                  workers_count=_WORKERS,
                                  max_tasks=_TASKS,
                                  scheduler=scheduler,
                                  max_workers=_MAX_WORKERS)

    _executor.start()

    _operations = _create(cif, scheduler)

    if config.getboolean('sampling', 'enable'):
        host.stats.start()

    for op in _operations:
        try:
            op.start()
        except Error as e:
            logging.warning('Operation not started: %s', e)


def stop():
    for op in _operations:
        op.stop()

    _executor.stop(wait=False)


class Operation(object):
    """
    Operation runs a callable with a given period until
    someone stops it.
    Operation builds on Schedule and on Executor,
    so that the underlying "func" is called periodically.
    It would be called again even if a former call is blocked.
    """

    _log = logging.getLogger("virt.periodic.Operation")

    def __init__(self, func, period, scheduler, timeout=0, executor=None,
                 exclusive=False, discard=True):
        """
        parameters:

        func: callable, without arguments (task interface).
        period: `func' will be invoked every `period' seconds.
                Please note that timing may not be exact due to
                (OS) scheduling constraings.
        timeout: same meaning of Executor.dispatch
        scheduler: Scheduler instance to use
        executor: Executor instance to use
        exclusive: boolean flag to control the exclusiveness of the operation.
                   Exclusive operations are scheduled again when and only when
                   completed (conservative approach). Non-exclusive operations
                   are scheduled again just after being dispatched to the
                   executor (optimistic approach).
                   The operations are non-exclusive by default.
        discard: boolean flag to pass to the underlying executor.
                 See the documentation of the 'Executor.dispatch' method.
        """
        self._func = func
        self._period = period
        self._timeout = _timeout_from(period) if timeout == 0 else timeout
        self._scheduler = scheduler
        self._executor = _executor if executor is None else executor
        self._exclusive = exclusive
        self._discard = discard
        self._lock = threading.Lock()
        self._running = False
        self._call = None
        self._name = str(self._func)

    def start(self):
        throttledlog.throttle(self._name, _THROTTLING_INTERVAL)
        with self._lock:
            if self._period <= 0:
                raise InvalidValue(repr(self), 'period', self._period)
            if self._running:
                raise AssertionError("Operation already running")
            self._log.debug("starting operation %s", self._func)
            self._running = True
            # we do _dispatch instead of _reschedule here to have some
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
        finally:
            if self._exclusive:
                self._reschedule()

    def _reschedule(self):
        """
        Schedule a next call of `func'.
        """
        self._call = self._scheduler.schedule(self._period,
                                              self._try_to_dispatch)

    def _try_to_dispatch(self):
        """
        Dispatch another Execution, if Operation is still running.
        """
        with self._lock:
            if self._running:
                self._dispatch()

    def _dispatch(self):
        """
        Send `func' to Executor to be run as soon as possible.
        """
        state = None
        self._call = None
        dispatched = False
        try:
            self._executor.dispatch(self, self._timeout, discard=self._discard)
            dispatched = True
        except exception.ResourceExhausted:
            self._log.warning('could not run %s, executor queue full',
                              self._func)
            state = repr(self._executor)
        finally:
            if not self._exclusive or not dispatched:
                self._reschedule()
        if state:
            throttledlog.warning(self._name, 'executor state: %s', state)

    def __repr__(self):
        return '<Operation action=%s at 0x%x>' % (
            self._func, id(self)
        )


class VmDispatcher(object):
    """
    Adapter class. Dispatch an Operation to all VMs, to improve
    isolation among them.
    """

    _log = logging.getLogger("virt.periodic.VmDispatcher")

    def __init__(self, get_vms, executor, create, timeout):
        """
        get_vms: callable which will return a dict which maps
                 vm_ids to vm_instances
        executor: executor.Executor instance
        create: callable to obtain the real callable to
                dispatch, with its timeout
        timeout: per-vm operation timeout, in seconds
                 (fractions allowed).
        """
        self._get_vms = get_vms
        self._executor = executor
        self._create = create
        self._timeout = timeout

    def __call__(self):
        vms = self._get_vms()
        skipped = []

        for vm_id, vm_obj in six.viewitems(vms):
            try:
                op = self._create(vm_obj)

                if not op.required:
                    continue
                # When dealing with blocked domains, we also want to avoid
                # to pile up jobs that libvirt can't handle and that will
                # eventually clog it.
                # We don't care too much about precise tracking, so it is
                # still OK if occasional misdetection occurs, but we
                # definitely want to avoid known-bad situation and to
                # needlessly overload libvirt.
                if not op.runnable:
                    skipped.append(vm_id)
                    continue

            except Exception:
                # we want to make sure to have VM UUID logged
                self._log.exception("while dispatching %s", op)
            else:
                try:
                    self._executor.dispatch(op, self._timeout)
                except exception.ResourceExhausted:
                    skipped.append(vm_id)

        if skipped:
            self._log.warning('could not run %s on %s',
                              self._create, skipped)
        return skipped  # for testing purposes

    def __repr__(self):
        return '<VmDispatcher operation=%s at 0x%x>' % (
            self._create, id(self)
        )


class _RunnableOnVm(object):
    def __init__(self, vm):
        self._vm = vm

    @property
    def required(self):
        # Disable everything until the migration destination VM
        # is fully started, to avoid false positives log spam.
        return self._vm.monitorable

    @property
    def runnable(self):
        return self._vm.isDomainReadyForCommands()

    def __call__(self):
        migrating = self._vm.isMigrating()
        try:
            self._execute()
        except virdomain.NotConnectedError:
            # race on startup:  no worries, let's retry again next cycle.
            # race on shutdown: next cycle won't pick up this VM.
            # both cases: let's reduce the log spam.
            self._vm.log.warning('could not run on %s: domain not connected',
                                 self._vm.id)
        except libvirt.libvirtError as e:
            if self._vm.post_copy != migration.PostCopyPhase.NONE:
                # race on entering post-copy, VM paused now
                return
            if e.get_error_code() in (
                # race on shutdown/migration completion
                libvirt.VIR_ERR_NO_DOMAIN,
            ):
                # known benign cases: migration in progress or completed
                if migrating or self._vm.lastStatus == vmstatus.DOWN:
                    return
            raise

    def _execute(self):
        raise NotImplementedError

    def __repr__(self):
        return '<%s vm=%s at 0x%x>' % (
            self.__class__.__name__, self._vm.id, id(self)
        )


class UpdateVolumes(_RunnableOnVm):

    @property
    def required(self):
        return (super(UpdateVolumes, self).required and
                # Avoid queries from storage during recovery process
                self._vm.volume_monitor.enabled())

    def _execute(self):
        for drive in self._vm.getDiskDevices():
            if not drive.readonly:
                # TODO: If this blocks we must make sure we don't overwrite
                # good data with stale old data.
                self._vm.updateDriveVolume(drive)


class BlockjobMonitor(_RunnableOnVm):

    @property
    def required(self):
        # For performance reasons, we must avoid as much
        # as possible to create per-vm executor tasks, even
        # though they will do nothing but a few checks and exit
        # early, as they do if a VM doesn't have Block Jobs to
        # monitor (most often true).
        return (super(BlockjobMonitor, self).required and self._vm.hasVmJobs)

    def _execute(self):
        self._vm.updateVmJobs()


class VolumeWatermarkMonitor(_RunnableOnVm):

    @property
    def required(self):
        return (super(VolumeWatermarkMonitor, self).required and
                self._vm.volume_monitor.monitoring_needed())

    def _execute(self):
        self._vm.monitor_volumes()


class _ExternalDataMonitor(_RunnableOnVm):
    KIND = None

    @property
    def required(self):
        # TPM data is normally initialized in Vm constructor, with the
        # exception of live migrations where it is transferred by
        # libvirt, after the migration is started.
        return self._vm.lastStatus != vmstatus.MIGRATION_DESTINATION

    @property
    def runnable(self):
        # This is not dependent on libvirt/QEMU health.
        return True

    def _execute(self):
        try:
            self._vm.update_external_data(self.KIND)
        except Exception as e:
            if self._vm.lastStatus == vmstatus.UP:
                log = self._vm.log.error
            else:
                log = self._vm.log.info
            log("Periodic external data retrieval failed (%s): %s",
                self.KIND, e)


class TpmDataMonitor(_ExternalDataMonitor):
    KIND = ExternalDataKind.TPM


class NvramDataMonitor(_ExternalDataMonitor):
    KIND = ExternalDataKind.NVRAM


def _kill_long_paused_vms(cif):
    log = logging.getLogger("virt.periodic")
    log.debug("Looking for stale paused VMs")
    for vm in cif.getVMs().values():
        if vm.lastStatus == vmstatus.PAUSED and \
           vm.pause_code in ('EIO', 'EOTHER',):
            vm.maybe_kill_paused()


def _create(cif, scheduler):
    def per_vm_operation(func, period):
        disp = VmDispatcher(
            cif.getVMs, _executor, func, _timeout_from(period))
        return Operation(disp, period, scheduler)

    ops = [
        # Needs dispatching because updating the volume stats needs
        # access to the storage, thus can block.
        per_vm_operation(
            UpdateVolumes,
            config.getint('irs', 'vol_size_sample_interval')),

        # Job monitoring need QEMU monitor access.
        per_vm_operation(
            BlockjobMonitor,
            config.getint('vars', 'vm_sample_jobs_interval')),

        # We do this only until we get high water mark notifications
        # from QEMU. It accesses storage and/or QEMU monitor, so can block,
        # thus we need dispatching.
        per_vm_operation(
            VolumeWatermarkMonitor,
            config.getint('vars', 'vm_watermark_interval')),

        per_vm_operation(
            NvramDataMonitor,
            config.getint('sampling', 'nvram_data_update_interval')),

        per_vm_operation(
            TpmDataMonitor,
            config.getint('sampling', 'tpm_data_update_interval')),

        Operation(
            lambda: recovery.lookup_external_vms(cif),
            config.getint('sampling', 'external_vm_lookup_interval'),
            scheduler,
            exclusive=True,
            discard=False),

        Operation(
            lambda: _kill_long_paused_vms(cif),
            vm_kill_paused_timeout() // 2,
            scheduler,
            exclusive=True,
            discard=False),
    ]

    if config.getboolean('sampling', 'enable'):
        ops.extend([
            # libvirt sampling using bulk stats can block, but unresponsive
            # domains are handled inside VMBulkstatsMonitor for performance
            # reasons; thus, does not need dispatching.
            Operation(
                sampling.VMBulkstatsMonitor(
                    libvirtconnection.get(cif),
                    cif.getVMs,
                    sampling.stats_cache),
                config.getint('vars', 'vm_sample_interval'),
                scheduler),

            Operation(
                sampling.HostMonitor(cif=cif),
                config.getint('vars', 'host_sample_stats_interval'),
                scheduler,
                timeout=config.getint('vars', 'host_sample_stats_interval'),
                exclusive=True,
                discard=False),
        ])

    return ops
