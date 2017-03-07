#
# Copyright 2008-2014 Red Hat, Inc.
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

import collections
import threading
import time
import libvirt

from vdsm import concurrent
from vdsm import hooks
from vdsm import kaxmlrpclib
from vdsm.common import response
from vdsm import utils
from vdsm import vdscli
from vdsm import jsonrpcvdscli
from vdsm.compat import pickle
from vdsm.config import config
from vdsm.common.define import NORMAL, Mbytes
from vdsm.common.network.address import normalize_literal_addr
from vdsm.sslcompat import sslutils
from vdsm.virt.utils import DynamicBoundedSemaphore
from yajsonrpc import \
    JsonRpcNoResponseError, \
    JsonRpcBindingsError


from vdsm.virt import vmexitreason
from vdsm.virt import vmstatus


MODE_REMOTE = 'remote'
MODE_FILE = 'file'


METHOD_ONLINE = 'online'


VIR_MIGRATE_PARAM_URI = 'migrate_uri'
VIR_MIGRATE_PARAM_BANDWIDTH = 'bandwidth'
VIR_MIGRATE_PARAM_GRAPHICS_URI = 'graphics_uri'


incomingMigrations = DynamicBoundedSemaphore(
    max(1, config.getint('vars', 'max_incoming_migrations')))


CONVERGENCE_SCHEDULE_SET_DOWNTIME = "setDowntime"
CONVERGENCE_SCHEDULE_POST_COPY = "postcopy"
CONVERGENCE_SCHEDULE_SET_ABORT = "abort"


_MiB_IN_GiB = 1024


class MigrationDestinationSetupError(RuntimeError):
    """
    Failed to create migration destination VM.
    """


class MigrationLimitExceeded(RuntimeError):
    """
    Cannot migrate right now: no resources on destination.
    """


class PostCopyPhase:
    NONE = 0
    REQUESTED = 1
    RUNNING = 2


class SourceThread(object):
    """
    A thread that takes care of migration on the source vdsm.
    """
    ongoingMigrations = DynamicBoundedSemaphore(1)

    def __init__(self, vm, dst='', dstparams='',
                 mode=MODE_REMOTE, method=METHOD_ONLINE,
                 tunneled=False, dstqemu='', abortOnError=False,
                 consoleAddress=None, compressed=False,
                 autoConverge=False, recovery=False, **kwargs):
        self.log = vm.log
        self._vm = vm
        self._dst = dst
        self._mode = mode
        if method != METHOD_ONLINE:
            self.log.warning(
                'migration method %s is deprecated, forced to "online"',
                method)
        self._dstparams = dstparams
        self._enableGuestEvents = kwargs.get('enableGuestEvents', False)
        self._machineParams = {}
        # TODO: utils.tobool shouldn't be used in this constructor, the
        # conversions should be handled properly in the API layer
        self._tunneled = utils.tobool(tunneled)
        self._abortOnError = utils.tobool(abortOnError)
        self._consoleAddress = consoleAddress
        self._dstqemu = dstqemu
        self._downtime = kwargs.get('downtime') or \
            config.get('vars', 'migration_downtime')
        self._maxBandwidth = int(
            kwargs.get('maxBandwidth') or
            config.getint('vars', 'migration_max_bandwidth')
        )
        self._autoConverge = utils.tobool(autoConverge)
        self._compressed = utils.tobool(compressed)
        self._incomingLimit = kwargs.get('incomingLimit')
        self._outgoingLimit = kwargs.get('outgoingLimit')
        self.status = {
            'status': {
                'code': 0,
                'message': 'Migration in progress'}}
        # we need to guard against concurrent updates only
        self._lock = threading.Lock()
        self._progress = 0
        self._thread = concurrent.thread(
            self.run, name='migsrc/' + self._vm.id[:8])
        self._preparingMigrationEvt = True
        self._migrationCanceledEvt = threading.Event()
        self._monitorThread = None
        self._destServer = None
        self._convergence_schedule = {
            'init': [],
            'stalling': []
        }
        self._use_convergence_schedule = False
        if 'convergenceSchedule' in kwargs:
            self._convergence_schedule = kwargs.get('convergenceSchedule')
            self._use_convergence_schedule = True
            self.log.debug('convergence schedule set to: %s',
                           str(self._convergence_schedule))
        self._started = False
        self._recovery = recovery

    def start(self):
        self._thread.start()

    def is_alive(self):
        return self._thread.is_alive()

    def migrating(self):
        """
        Return whether the thread currently manages a migration.

        That can be a migration directly supervised by the source thread and
        other threads (such as the downtime thread) or just an indirectly
        managed migration (detected on Vdsm recovery) without the threads
        actually running.
        """
        return (self.is_alive() or
                (self._recovery and
                 self._vm.lastStatus == vmstatus.MIGRATION_SOURCE))

    @property
    def started(self):
        return self._started

    @property
    def hibernating(self):
        return self._mode == MODE_FILE

    def _update_progress(self):
        if self._monitorThread is None:
            return

        # fetch migration status from the monitor thread
        if self._monitorThread.progress is not None:
            progress = self._monitorThread.progress.percentage
        else:
            progress = 0

        with self._lock:
            old_progress = self._progress
            if progress >= old_progress:
                self._progress = progress

        if progress < old_progress:
            self.log.info(
                'new computed progress %d < than old value %d, discarded',
                progress, old_progress)

    def getStat(self):
        """
        Get the status of the migration.
        """
        self._update_progress()
        self.status['progress'] = self._progress
        return self.status

    def _createClient(self, port):
        sslctx = sslutils.create_ssl_context()

        def is_ipv6_address(a):
            return (':' in a) and a.startswith('[') and a.endswith(']')

        if is_ipv6_address(self.remoteHost):
            host = self.remoteHost[1:-1]
        else:
            host = self.remoteHost

        client_socket = utils.create_connected_socket(host, int(port), sslctx)
        return self._vm.cif.createStompClient(client_socket)

    def _setupVdsConnection(self):
        if self.hibernating:
            return

        hostPort = vdscli.cannonizeHostPort(
            self._dst,
            config.getint('addresses', 'management_port'))
        self.remoteHost, port = hostPort.rsplit(':', 1)

        try:
            client = self._createClient(port)
            requestQueues = config.get('addresses', 'request_queues')
            requestQueue = requestQueues.split(",")[0]
            self._destServer = jsonrpcvdscli.connect(requestQueue, client)
            self.log.debug('Initiating connection with destination')
            self._destServer.ping()

        except (JsonRpcBindingsError, JsonRpcNoResponseError):
            if config.getboolean('vars', 'ssl'):
                self._destServer = vdscli.connect(
                    hostPort,
                    useSSL=True,
                    TransportClass=kaxmlrpclib.TcpkeepSafeTransport)
            else:
                self._destServer = kaxmlrpclib.Server('http://' + hostPort)

        self.log.debug('Destination server is: ' + hostPort)

    def _setupRemoteMachineParams(self):
        self._machineParams.update(self._vm.status())
        # patch VM config for targets < 3.1
        self._patchConfigForLegacy()
        self._machineParams['elapsedTimeOffset'] = \
            time.time() - self._vm._startTime
        vmStats = self._vm.getStats()
        if 'username' in vmStats:
            self._machineParams['username'] = vmStats['username']
        if 'guestIPs' in vmStats:
            self._machineParams['guestIPs'] = vmStats['guestIPs']
        if 'guestFQDN' in vmStats:
            self._machineParams['guestFQDN'] = vmStats['guestFQDN']
        self._machineParams['guestAgentAPIVersion'] = \
            self._vm.guestAgent.effectiveApiVersion
        for k in ('_migrationParams', 'pid'):
            if k in self._machineParams:
                del self._machineParams[k]
        if not self.hibernating:
            self._machineParams['migrationDest'] = 'libvirt'
        self._machineParams['_srcDomXML'] = self._vm._dom.XMLDesc(0)
        self._machineParams['enableGuestEvents'] = self._enableGuestEvents

    def _prepareGuest(self):
        if self.hibernating:
            self.log.debug("Save State begins")
            if self._vm.guestAgent.isResponsive():
                lockTimeout = 30
            else:
                lockTimeout = 0
            self._vm.guestAgent.desktopLock()
            # wait for lock or timeout
            while lockTimeout:
                if self._vm.getStats()['session'] in ["Locked", "LoggedOff"]:
                    break
                time.sleep(1)
                lockTimeout -= 1
                if lockTimeout == 0:
                    self.log.warning('Agent ' + self._vm.id +
                                     ' unresponsive. Hiberanting without '
                                     'desktopLock.')
                    break
            self._vm.pause(vmstatus.SAVING_STATE)
        else:
            self.log.debug("Migration started")
            self._vm.lastStatus = vmstatus.MIGRATION_SOURCE

    def _recover(self, message):
        if not response.is_error(self.status):
            self.status = response.error('migrateErr')
        self.log.error(message)
        if not self.hibernating and self._destServer is not None:
            if self._vm.post_copy == PostCopyPhase.RUNNING:
                # We can't recover a VM after a failed post-copy migration.
                # And the destination takes care of the situation itself.
                self._vm.handle_failed_post_copy(clean_vm=True)
                return
            try:
                self._destServer.destroy(self._vm.id)
            except Exception:
                self.log.exception("Failed to destroy remote VM")
        # if the guest was stopped before migration, we need to cont it
        if self.hibernating:
            self._vm.cont(ignoreStatus=True)
            if self._enableGuestEvents:
                self._vm.guestAgent.events.after_hibernation_failure()
        elif self._enableGuestEvents:
            self._vm.guestAgent.events.after_migration_failure()
        # either way, migration has finished
        if self._recovery:
            self._vm.set_last_status(vmstatus.UP, vmstatus.MIGRATION_SOURCE)
            self._recovery = False
        else:
            self._vm.lastStatus = vmstatus.UP
        self._started = False
        self._vm.send_status_event()

    def _finishSuccessfully(self):
        with self._lock:
            self._progress = 100
        if not self.hibernating:
            # TODO: We could use a timeout on the wait to be more robust
            # against "impossible" failures. But we don't have a good value to
            # use here now.
            self._vm.stopped_migrated_event_processed.wait()
            self._vm.setDownStatus(NORMAL, vmexitreason.MIGRATION_SUCCEEDED)
            self.status['status']['message'] = 'Migration done'
            if self._vm.post_copy == PostCopyPhase.RUNNING:
                self._vm.destroy()
        else:
            # don't pickle transient params
            for ignoreParam in ('displayIp', 'display', 'pid'):
                if ignoreParam in self._machineParams:
                    del self._machineParams[ignoreParam]

            fname = self._vm.cif.prepareVolumePath(self._dstparams)
            try:
                # Use r+ to avoid truncating the file, see BZ#1282239
                with open(fname, "r+") as f:
                    pickle.dump(self._machineParams, f)
            finally:
                self._vm.cif.teardownVolumePath(self._dstparams)

            self._vm.setDownStatus(NORMAL, vmexitreason.SAVE_STATE_SUCCEEDED)
            self.status['status']['message'] = 'SaveState done'

    def _patchConfigForLegacy(self):
        """
        Remove from the VM config drives list "cdrom" and "floppy"
        items and set them up as full paths
        """
        # care only about "drives" list, since
        # "devices" doesn't cause errors
        if 'drives' in self._machineParams:
            for item in ("cdrom", "floppy"):
                new_drives = []
                for drive in self._machineParams['drives']:
                    if drive['device'] == item:
                        self._machineParams[item] = drive['path']
                    else:
                        new_drives.append(drive)
                self._machineParams['drives'] = new_drives

        # vdsm < 4.13 expect this to exist
        self._machineParams['afterMigrationStatus'] = ''

    @staticmethod
    def _raiseAbortError():
        e = libvirt.libvirtError(defmsg='')
        # we have to override the value to get what we want
        # err might be None
        e.err = (libvirt.VIR_ERR_OPERATION_ABORTED,  # error code
                 libvirt.VIR_FROM_QEMU,              # error domain
                 'operation aborted',                # error message
                 libvirt.VIR_ERR_WARNING,            # error level
                 '', '', '',                         # str1, str2, str3,
                 -1, -1)                             # int1, int2
        raise e

    def _update_outgoing_limit(self):
        if self._outgoingLimit:
            self.log.debug('Setting outgoing migration limit to %s',
                           self._outgoingLimit)
            SourceThread.ongoingMigrations.bound = self._outgoingLimit

    @property
    def recovery(self):
        """
        Return whether the source thread handles a recovered migration.

        This is when we detect the VM is migrating in Vdsm recovery and the
        source thread is not actually running.

        This serves to handle a possible already running migration detected
        during Vdsm recovery, for which no regular source thread exists.  We
        don't try to touch such a migration, but we still must ensure at least
        basic sanity:

        - Indication that the migration is running.
        - Canceling the migration.
        - Putting the VM into proper status after migration failure (in case
          the migration succeeds, we rely on the fact that the VM disappears
          and Vdsm detects that sooner or later).

        .. note::

           Just setting this flag doesn't mean that any migration is actually
           running, it just means that if a migration is running then the
           migration was started by another Vdsm instance.  When this flag is
           set then the VM may be actually migrating only if its status is
           `vmstatus.MIGRATION_SOURCE` or `vmstatus.WAIT_FOR_LAUNCH` (the
           latter is mostly irrelevant since we prevent most actions in that
           status).
        """
        return self._recovery

    def run(self):
        self._recovery = False
        self._update_outgoing_limit()
        try:
            startTime = time.time()
            self._setupVdsConnection()
            self._setupRemoteMachineParams()
            self._prepareGuest()

            while not self._started:
                try:
                    with SourceThread.ongoingMigrations:
                        timeout = config.getint(
                            'vars', 'guest_lifecycle_event_reply_timeout')
                        if self.hibernating:
                            self._vm.guestAgent.events.before_hibernation(
                                wait_timeout=timeout)
                        elif self._enableGuestEvents:
                            self._vm.guestAgent.events.before_migration(
                                wait_timeout=timeout)
                        if self._migrationCanceledEvt.is_set():
                            self._raiseAbortError()
                        self.log.debug("migration semaphore acquired "
                                       "after %d seconds",
                                       time.time() - startTime)
                        params = {
                            'dst': self._dst,
                            'mode': self._mode,
                            'method': METHOD_ONLINE,
                            'dstparams': self._dstparams,
                            'dstqemu': self._dstqemu,
                        }
                        with self._vm.migration_parameters(params):
                            self._vm.saveState()
                            self._startUnderlyingMigration(time.time())
                            self._finishSuccessfully()
                except libvirt.libvirtError as e:
                    if e.get_error_code() == libvirt.VIR_ERR_OPERATION_ABORTED:
                        self.status = response.error(
                            'migCancelErr', message='Migration canceled')
                    raise
                except MigrationLimitExceeded:
                    retry_timeout = config.getint('vars',
                                                  'migration_retry_timeout')
                    self.log.debug("Migration destination busy. Initiating "
                                   "retry in %d seconds.", retry_timeout)
                    self._migrationCanceledEvt.wait(retry_timeout)
        except MigrationDestinationSetupError as e:
            self._recover(str(e))
            # we know what happened, no need to dump hollow stack trace
        except Exception as e:
            self._recover(str(e))
            self.log.exception("Failed to migrate")

    def _startUnderlyingMigration(self, startTime):
        if self.hibernating:
            hooks.before_vm_hibernate(self._vm._dom.XMLDesc(0), self._vm.conf)
            fname = self._vm.cif.prepareVolumePath(self._dst)
            try:
                self._vm._dom.save(fname)
            finally:
                self._vm.cif.teardownVolumePath(self._dst)
        else:
            for dev in self._vm._customDevices():
                hooks.before_device_migrate_source(
                    dev._deviceXML, self._vm.conf, dev.custom)
            hooks.before_vm_migrate_source(self._vm._dom.XMLDesc(0),
                                           self._vm.conf)

            # Do not measure the time spent for creating the VM on the
            # destination. In some cases some expensive operations can cause
            # the migration to get cancelled right after the transfer started.
            destCreateStartTime = time.time()
            result = self._destServer.migrationCreate(self._machineParams,
                                                      self._incomingLimit)
            destCreationTime = time.time() - destCreateStartTime
            startTime += destCreationTime
            self.log.info('Creation of destination VM took: %d seconds',
                          destCreationTime)

            if response.is_error(result):
                self.status = result
                if response.is_error(result, 'migrateLimit'):
                    raise MigrationLimitExceeded()
                else:
                    raise MigrationDestinationSetupError(
                        'migration destination error: ' +
                        result['status']['message'])

            self._started = True

            if config.getboolean('vars', 'ssl'):
                transport = 'tls'
            else:
                transport = 'tcp'
            duri = 'qemu+{}://{}/system'.format(
                transport, normalize_literal_addr(self.remoteHost))

            dstqemu = self._vm.conf['_migrationParams']['dstqemu']
            if dstqemu:
                muri = 'tcp://{}'.format(
                    normalize_literal_addr(dstqemu))
            else:
                muri = 'tcp://{}'.format(
                    normalize_literal_addr(self.remoteHost))

            self._vm.log.info('starting migration to %s '
                              'with miguri %s', duri, muri)

            self._monitorThread = MonitorThread(self._vm, startTime,
                                                self._convergence_schedule,
                                                self._use_convergence_schedule)

            if self._use_convergence_schedule:
                self._perform_with_conv_schedule(duri, muri)
            else:
                self._perform_with_downtime_thread(duri, muri)

            self.log.info("migration took %d seconds to complete",
                          (time.time() - startTime) + destCreationTime)

    def _perform_migration(self, duri, muri):
        if self._vm.hasSpice and self._vm.conf.get('clientIp'):
            SPICE_MIGRATION_HANDOVER_TIME = 120
            self._vm._reviveTicket(SPICE_MIGRATION_HANDOVER_TIME)

        # FIXME: there still a race here with libvirt,
        # if we call stop() and libvirt migrateToURI3 didn't start
        # we may return migration stop but it will start at libvirt
        # side
        self._preparingMigrationEvt = False
        if not self._migrationCanceledEvt.is_set():
            # TODO: use libvirt constants when bz#1222795 is fixed
            params = {VIR_MIGRATE_PARAM_BANDWIDTH: self._maxBandwidth}
            if not self._tunneled:
                params[VIR_MIGRATE_PARAM_URI] = str(muri)
            if self._consoleAddress:
                if self._vm.hasSpice:
                    graphics = 'spice'
                else:
                    graphics = 'vnc'
                params[VIR_MIGRATE_PARAM_GRAPHICS_URI] = str('%s://%s' % (
                    graphics, self._consoleAddress))

            flags = (libvirt.VIR_MIGRATE_LIVE |
                     libvirt.VIR_MIGRATE_PEER2PEER |
                     (libvirt.VIR_MIGRATE_TUNNELLED if
                         self._tunneled else 0) |
                     (libvirt.VIR_MIGRATE_ABORT_ON_ERROR if
                         self._abortOnError else 0) |
                     (libvirt.VIR_MIGRATE_COMPRESSED if
                         self._compressed else 0) |
                     (libvirt.VIR_MIGRATE_AUTO_CONVERGE if
                         self._autoConverge else 0) |
                     self._post_copy_flag(self._convergence_schedule))

            self._vm._dom.migrateToURI3(duri, params, flags)
        else:
            self._raiseAbortError()

    def _post_copy_flag(self, convergence_schedule):
        # Migration may fail immediately when VIR_MIGRATE_POSTCOPY flag is
        # present in the following situations:
        # - The transport is not capable of full bidirectional
        #   connectivity: RDMA, tunnelled, pipe.
        # - Huge pages are used (doesn't apply to transparent huge pages).
        # - QEMU uses a file as a backing for memory.
        # - Perhaps non-shared block storage may cause some trouble.
        for s in self._convergence_schedule.get('stalling', []):
            action = s.get('action', {}).get('name')
            if action == CONVERGENCE_SCHEDULE_POST_COPY:
                return libvirt.VIR_MIGRATE_POSTCOPY
        return 0

    def _perform_with_downtime_thread(self, duri, muri):
        self._vm.log.debug('performing migration with downtime thread')
        self._monitorThread.downtime_thread = DowntimeThread(
            self._vm,
            int(self._downtime),
            config.getint('vars', 'migration_downtime_steps')
        )

        with utils.running(self._monitorThread):
            self._perform_migration(duri, muri)

        self._monitorThread.join()

    def _perform_with_conv_schedule(self, duri, muri):
        self._vm.log.debug('performing migration with conv schedule')
        with utils.running(self._monitorThread):
            self._perform_migration(duri, muri)
        self._monitorThread.join()

    def set_max_bandwidth(self, bandwidth):
        self._vm.log.debug('setting migration max bandwidth to %d', bandwidth)
        self._maxBandwidth = bandwidth
        self._vm._dom.migrateSetMaxSpeed(bandwidth)

    def stop(self):
        # if its locks we are before the migrateToURI3()
        # call so no need to abortJob()
        try:
            self._migrationCanceledEvt.set()
            self._vm._dom.abortJob()
        except libvirt.libvirtError:
            if not self._preparingMigrationEvt:
                raise
        if self._recovery:
            self._recover("Migration stopped")

    def recovery_cleanup(self):
        """
        Finish and cleanup recovery migration if necessary.

        This is to handle the situation when we detect a failed migration
        outside the source thread.  The source thread usually handles failed
        migrations itself.  But the thread is not running after recovery so in
        such a case the source thread must be notified about the failed
        migration.  This is what this method serves for.
        """
        if self._recovery and \
           self._vm.lastStatus == vmstatus.MIGRATION_SOURCE:
            self._recover("Migration failed")


def exponential_downtime(downtime, steps):
    if steps > 1:
        offset = downtime / float(steps)
        base = (downtime - offset) ** (1 / float(steps - 1))

        for i in range(steps):
            yield int(offset + base ** i)
    else:
        yield downtime


class DowntimeThread(object):

    # avoid grow too large for large VMs
    _WAIT_STEP_LIMIT = 60  # seconds

    def __init__(self, vm, downtime, steps):
        self._vm = vm
        self._downtime = downtime
        self._steps = steps
        self._stop = threading.Event()

        delay_per_gib = config.getint('vars', 'migration_downtime_delay')
        memSize = int(vm.conf['memSize'])
        self._wait = min(
            delay_per_gib * memSize / (_MiB_IN_GiB * self._steps),
            self._WAIT_STEP_LIMIT)
        # do not materialize, keep as generator expression
        self._downtimes = exponential_downtime(self._downtime, self._steps)
        # we need the first value to support set_initial_downtime
        self._initial_downtime = next(self._downtimes)

        self._thread = concurrent.thread(
            self.run, name='migdwn/' + self._vm.id[:8])

    def start(self):
        self._thread.start()

    def join(self):
        self._thread.join()

    def is_alive(self):
        return self._thread.is_alive()

    @utils.traceback()
    def run(self):
        self._vm.log.debug('migration downtime thread started (%i steps)',
                           self._steps)

        for downtime in self._downtimes:
            if self._stop.is_set():
                break

            self._set_downtime(downtime)

            self._stop.wait(self._wait)

        self._vm.log.debug('migration downtime thread exiting')

    def set_initial_downtime(self):
        self._set_downtime(self._initial_downtime)

    def stop(self):
        self._vm.log.debug('stopping migration downtime thread')
        self._stop.set()

    def _set_downtime(self, downtime):
        self._vm.log.debug('setting migration downtime to %d', downtime)
        self._vm._dom.migrateSetMaxDowntime(downtime, 0)


# we introduce this empty fake so the monitoring code doesn't have
# to distinguish between no DowntimeThread and DowntimeThread present.
class _FakeThreadInterface(object):

    def start(self):
        pass

    def stop(self):
        pass

    def join(self):
        pass

    def is_alive(self):
        return False

    def set_initial_downtime(self):
        pass


class MonitorThread(object):
    _MIGRATION_MONITOR_INTERVAL = config.getint(
        'vars', 'migration_monitor_interval')  # seconds

    def __init__(self, vm, startTime, conv_schedule, use_conv_schedule):
        super(MonitorThread, self).__init__()
        self._stop = threading.Event()
        self._vm = vm
        self._startTime = startTime
        self.daemon = True
        self.progress = None
        self._conv_schedule = conv_schedule
        self._use_conv_schedule = use_conv_schedule
        self.downtime_thread = _FakeThreadInterface()
        self._thread = concurrent.thread(
            self.run, name='migmon/' + self._vm.id[:8])

    def start(self):
        self._thread.start()

    def join(self):
        self._thread.join()

    @property
    def enabled(self):
        return MonitorThread._MIGRATION_MONITOR_INTERVAL > 0

    @utils.traceback()
    def run(self):
        if self.enabled:
            self._vm.log.debug('starting migration monitor thread')
            try:
                self.monitor_migration()
            finally:
                self.downtime_thread.stop()
            if self.downtime_thread.is_alive():
                # on very short migrations, the downtime thread
                # may not be started at all.
                self.downtime_thread.join()
            self._vm.log.debug('stopped migration monitor thread')
        else:
            self._vm.log.info('migration monitor thread disabled'
                              ' (monitoring interval set to 0)')

    def monitor_migration(self):
        memSize = int(self._vm.conf['memSize'])
        maxTimePerGiB = config.getint('vars',
                                      'migration_max_time_per_gib_mem')
        migrationMaxTime = (maxTimePerGiB * memSize + 1023) / 1024
        progress_timeout = config.getint('vars', 'migration_progress_timeout')
        lastProgressTime = time.time()
        lowmark = None
        lastDataRemaining = None
        iterationCount = 0

        self._execute_init(self._conv_schedule['init'])
        if not self._use_conv_schedule:
            self._vm.log.debug('setting initial migration downtime')
            self.downtime_thread.set_initial_downtime()

        while not self._stop.isSet():
            stopped = self._stop.wait(self._MIGRATION_MONITOR_INTERVAL)
            if stopped:
                break

            job_stats = self._vm._dom.jobStats()
            # It may happen that the migration did not start yet
            # so we'll keep waiting
            if not ongoing(job_stats):
                continue

            progress = Progress.from_job_stats(job_stats)
            self._vm.send_migration_status_event()

            now = time.time()
            if self._vm.post_copy != PostCopyPhase.NONE:
                # Post-copy mode is a final state of a migration -- it either
                # completes or fails and stops the VM, there is no way to
                # continue with the migration in either case.  So we won't
                # handle any further schedule actions once post-copy is
                # successfully started.  It's still recommended to put the
                # abort action after the post-copy action in the schedule, for
                # the case when it's not possible to switch to the post-copy
                # mode for some reason.
                if self._vm.post_copy == PostCopyPhase.RUNNING:
                    # If post-copy is not RUNNING then we are in the interim
                    # phase (which should be short) between initiating the
                    # post-copy migration and the actual start of the post-copy
                    # migration.  Nothing needs to be done in that case.
                    self._vm.log.debug(
                        'Post-copy migration still in progress: %d',
                        progress.data_remaining
                    )
            elif not self._use_conv_schedule and\
                    (0 < migrationMaxTime < now - self._startTime):
                self._vm.log.warn('The migration took %d seconds which is '
                                  'exceeding the configured maximum time '
                                  'for migrations of %d seconds. The '
                                  'migration will be aborted.',
                                  now - self._startTime,
                                  migrationMaxTime)
                self._vm._dom.abortJob()
                self.stop()
                break
            elif (lowmark is None) or (lowmark > progress.data_remaining):
                lowmark = progress.data_remaining
                lastProgressTime = now
            else:
                self._vm.log.warn(
                    'Migration stalling: remaining (%sMiB)'
                    ' > lowmark (%sMiB).'
                    ' Refer to RHBZ#919201.',
                    progress.data_remaining / Mbytes, lowmark / Mbytes)

            if not self._vm.post_copy and\
                    lastDataRemaining is not None and\
                    lastDataRemaining < progress.data_remaining:
                iterationCount += 1
                self._vm.log.debug('new iteration detected: %i',
                                   iterationCount)
                if self._use_conv_schedule:
                    self._next_action(iterationCount)
                elif iterationCount == 1:
                    # it does not make sense to do any adjustments before
                    # first iteration.
                    self.downtime_thread.start()

            lastDataRemaining = progress.data_remaining

            if not self._use_conv_schedule and\
                    (now - lastProgressTime) > progress_timeout:
                # Migration is stuck, abort
                self._vm.log.warn(
                    'Migration is stuck: Hasn\'t progressed in %s seconds. '
                    'Aborting.' % (now - lastProgressTime))
                self._vm._dom.abortJob()
                self.stop()

            if self._stop.isSet():
                break

            self.progress = progress
            self._vm.log.info('%s', progress)

    def stop(self):
        self._vm.log.debug('stopping migration monitor thread')
        self._stop.set()

    def _next_action(self, stalling):
        head = self._conv_schedule['stalling'][0]

        self._vm.log.debug('Stalling for %d iterations, '
                           'checking to make next action: '
                           '%s', stalling, head)
        if head['limit'] < stalling:
            self._execute_action_with_params(head['action'])
            self._conv_schedule['stalling'].pop(0)
            self._vm.log.debug('setting conv schedule to: %s',
                               self._conv_schedule)

    def _execute_init(self, init_actions):
        for action_with_params in init_actions:
            self._execute_action_with_params(action_with_params)

    def _execute_action_with_params(self, action_with_params):
        action = str(action_with_params['name'])
        vm = self._vm
        if action == CONVERGENCE_SCHEDULE_SET_DOWNTIME:
            downtime = int(action_with_params['params'][0])
            vm.log.debug('Setting downtime to %d', downtime)
            vm._dom.migrateSetMaxDowntime(downtime, 0)
        elif action == CONVERGENCE_SCHEDULE_POST_COPY:
            if not self._vm.switch_migration_to_post_copy():
                # Do nothing for now; the next action will be invoked after a
                # while
                vm.log.warn('Failed to switch to post-copy migration')
        elif action == CONVERGENCE_SCHEDULE_SET_ABORT:
            vm.log.warn('Aborting migration')
            vm._dom.abortJob()
            self.stop()


_Progress = collections.namedtuple('_Progress', [
    'job_type', 'time_elapsed', 'data_total',
    'data_processed', 'data_remaining',
    'mem_total', 'mem_processed', 'mem_remaining',
    'mem_bps', 'mem_constant', 'compression_bytes',
    'dirty_rate', 'mem_iteration'
])


class Progress(_Progress):
    __slots__ = ()

    @classmethod
    def from_job_stats(cls, stats):
        return cls(
            stats['type'],
            stats[libvirt.VIR_DOMAIN_JOB_TIME_ELAPSED],
            stats[libvirt.VIR_DOMAIN_JOB_DATA_TOTAL],
            stats[libvirt.VIR_DOMAIN_JOB_DATA_PROCESSED],
            stats[libvirt.VIR_DOMAIN_JOB_DATA_REMAINING],
            stats[libvirt.VIR_DOMAIN_JOB_MEMORY_TOTAL],
            stats[libvirt.VIR_DOMAIN_JOB_MEMORY_PROCESSED],
            stats[libvirt.VIR_DOMAIN_JOB_MEMORY_REMAINING],
            stats.get(libvirt.VIR_DOMAIN_JOB_MEMORY_BPS, 0),
            stats.get(libvirt.VIR_DOMAIN_JOB_MEMORY_CONSTANT, 0),
            stats.get(libvirt.VIR_DOMAIN_JOB_COMPRESSION_BYTES, 0),
            # available since libvirt 1.3
            stats.get('memory_dirty_rate', -1),
            # available since libvirt 1.3
            stats.get('memory_iteration', -1),
        )

    def __str__(self):
        return (
            'Migration Progress: %s seconds elapsed,'
            ' %s%% of data processed,'
            ' total data: %iMB,'
            ' processed data: %iMB, remaining data: %iMB,'
            ' transfer speed %iMBps, zero pages: %iMB,'
            ' compressed: %iMB, dirty rate: %i,'
            ' memory iteration: %i' % (
                (self.time_elapsed / 1000),
                self.percentage,
                (self.data_total / Mbytes),
                (self.data_processed / Mbytes),
                (self.data_remaining / Mbytes),
                (self.mem_bps / Mbytes),
                self.mem_constant,
                (self.compression_bytes / Mbytes),
                self.dirty_rate,
                self.mem_iteration,
            )
        )

    @property
    def percentage(self):
        if self.data_remaining == 0 and self.data_total:
            return 100
        progress = 0
        if self.data_total:
            progress = 100 - 100 * self.data_remaining / self.data_total
        if progress < 100:
            return progress
        return 99


def ongoing(stats):
    try:
        job_type = stats['type']
    except KeyError:
        return False
    else:
        return job_type != libvirt.VIR_DOMAIN_JOB_NONE
