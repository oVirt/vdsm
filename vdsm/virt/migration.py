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

import threading
import time
import libvirt

import hooks
import kaxmlrpclib
from vdsm import response
from vdsm import utils
from vdsm import vdscli
from vdsm import jsonrpcvdscli
from vdsm.compat import pickle
from vdsm.config import config
from vdsm.define import NORMAL, Mbytes
from vdsm.sslcompat import sslutils
from yajsonrpc import \
    JsonRpcNoResponseError, \
    JsonRpcBindingsError


from . import vmexitreason
from . import vmstatus


MODE_REMOTE = 'remote'
MODE_FILE = 'file'


METHOD_ONLINE = 'online'


VIR_MIGRATE_PARAM_URI = 'migrate_uri'
VIR_MIGRATE_PARAM_BANDWIDTH = 'bandwidth'
VIR_MIGRATE_PARAM_GRAPHICS_URI = 'graphics_uri'


class MigrationDestinationSetupError(RuntimeError):
    """
    Failed to create migration destination VM.
    """


class SourceThread(threading.Thread):
    """
    A thread that takes care of migration on the source vdsm.
    """
    _ongoingMigrations = threading.BoundedSemaphore(1)

    @classmethod
    def setMaxOutgoingMigrations(cls, n):
        """Set the initial value of the _ongoingMigrations semaphore.

        must not be called after any vm has been run."""
        cls._ongoingMigrations = threading.BoundedSemaphore(n)

    def __init__(self, vm, dst='', dstparams='',
                 mode=MODE_REMOTE, method=METHOD_ONLINE,
                 tunneled=False, dstqemu='', abortOnError=False,
                 consoleAddress=None, compressed=False,
                 autoConverge=False, **kwargs):
        self.log = vm.log
        self._vm = vm
        self._dst = dst
        self._mode = mode
        if method != METHOD_ONLINE:
            self.log.warning(
                'migration method %s is deprecated, forced to "online"',
                method)
        self._dstparams = dstparams
        self._machineParams = {}
        self._tunneled = utils.tobool(tunneled)
        self._abortOnError = utils.tobool(abortOnError)
        self._consoleAddress = consoleAddress
        self._dstqemu = dstqemu
        self._downtime = kwargs.get('downtime') or \
            config.get('vars', 'migration_downtime')
        self._autoConverge = autoConverge
        self._compressed = compressed
        self.status = {
            'status': {
                'code': 0,
                'message': 'Migration in progress'}}
        self._progress = 0
        threading.Thread.__init__(self)
        self._preparingMigrationEvt = True
        self._migrationCanceledEvt = False
        self._monitorThread = None

    @property
    def hibernating(self):
        return self._mode == MODE_FILE

    def getStat(self):
        """
        Get the status of the migration.
        """
        if self._monitorThread is not None:
            # fetch migration status from the monitor thread
            self._progress = self._monitorThread.progress
        self.status['progress'] = self._progress

        stat = self._vm._dom.jobStats(libvirt.VIR_DOMAIN_JOB_STATS_COMPLETED)
        if 'downtime_net' in stat:
            self.status['downtime'] = stat['downtime_net']

        return self.status

    def _createClient(self, port):
        sslctx = sslutils.create_ssl_context()
        client_socket = utils.create_connected_socket(
            self.remoteHost, int(port), sslctx)
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
        for k in ('_migrationParams', 'pid'):
            if k in self._machineParams:
                del self._machineParams[k]
        if not self.hibernating:
            self._machineParams['migrationDest'] = 'libvirt'
        self._machineParams['_srcDomXML'] = self._vm._dom.XMLDesc(0)

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
        if not self.hibernating:
            try:
                self._destServer.destroy(self._vm.id)
            except Exception:
                self.log.exception("Failed to destroy remote VM")
        # if the guest was stopped before migration, we need to cont it
        if self.hibernating:
            self._vm.cont()
        # either way, migration has finished
        self._vm.lastStatus = vmstatus.UP
        self._vm.send_status_event()

    def _finishSuccessfully(self):
        self._progress = 100
        if not self.hibernating:
            self._vm.setDownStatus(NORMAL, vmexitreason.MIGRATION_SUCCEEDED)
            self.status['status']['message'] = 'Migration done'
        else:
            # don't pickle transient params
            for ignoreParam in ('displayIp', 'display', 'pid'):
                if ignoreParam in self._machineParams:
                    del self._machineParams[ignoreParam]

            fname = self._vm.cif.prepareVolumePath(self._dstparams)
            try:
                with open(fname, "w") as f:
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

    def run(self):
        try:
            startTime = time.time()
            self._setupVdsConnection()
            self._setupRemoteMachineParams()
            self._prepareGuest()
            with SourceThread._ongoingMigrations:
                try:
                    if self._migrationCanceledEvt:
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
            result = self._destServer.migrationCreate(self._machineParams)
            destCreationTime = time.time() - destCreateStartTime
            startTime += destCreationTime
            self.log.info('Creation of destination VM took: %d seconds',
                          destCreationTime)

            if response.is_error(result):
                self.status = result
                raise MigrationDestinationSetupError(
                    'migration destination error: ' +
                    result['status']['message'])
            if config.getboolean('vars', 'ssl'):
                transport = 'tls'
            else:
                transport = 'tcp'
            duri = 'qemu+%s://%s/system' % (transport, self.remoteHost)
            if self._vm.conf['_migrationParams']['dstqemu']:
                muri = 'tcp://%s' % \
                       self._vm.conf['_migrationParams']['dstqemu']
            else:
                muri = 'tcp://%s' % self.remoteHost

            self._vm.log.info('starting migration to %s '
                              'with miguri %s', duri, muri)

            downtimeThread = DowntimeThread(
                self._vm,
                int(self._downtime),
                config.getint('vars', 'migration_downtime_steps'))
            self._monitorThread = MonitorThread(self._vm, startTime)
            with utils.running(downtimeThread):
                with utils.running(self._monitorThread):
                    # we need to support python 2.6, so two nested with-s.
                    self._perform_migration(duri, muri)

            self.log.info("migration took %d seconds to complete",
                          (time.time() - startTime) + destCreationTime)

    def _perform_migration(self, duri, muri):
        if self._vm.hasSpice and self._vm.conf.get('clientIp'):
            SPICE_MIGRATION_HANDOVER_TIME = 120
            self._vm._reviveTicket(SPICE_MIGRATION_HANDOVER_TIME)

        maxBandwidth = config.getint('vars', 'migration_max_bandwidth')
        # FIXME: there still a race here with libvirt,
        # if we call stop() and libvirt migrateToURI3 didn't start
        # we may return migration stop but it will start at libvirt
        # side
        self._preparingMigrationEvt = False
        if not self._migrationCanceledEvt:
            # TODO: use libvirt constants when bz#1222795 is fixed
            params = {VIR_MIGRATE_PARAM_URI: str(muri),
                      VIR_MIGRATE_PARAM_BANDWIDTH: maxBandwidth}
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
                         self._autoConverge else 0))

            self._vm._dom.migrateToURI3(duri, params, flags)
        else:
            self._raiseAbortError()

    def stop(self):
        # if its locks we are before the migrateToURI3()
        # call so no need to abortJob()
        try:
            self._migrationCanceledEvt = True
            self._vm._dom.abortJob()
        except libvirt.libvirtError:
            if not self._preparingMigrationEvt:
                raise


def exponential_downtime(downtime, steps):
    offset = downtime / float(steps)
    base = (downtime - offset) ** (1 / float(steps - 1))

    for i in range(steps):
        yield int(offset + base ** i)


class DowntimeThread(threading.Thread):
    def __init__(self, vm, downtime, steps):
        super(DowntimeThread, self).__init__()

        self._vm = vm
        self._downtime = downtime
        self._steps = steps
        self._stop = threading.Event()

        delay_per_gib = config.getint('vars', 'migration_downtime_delay')
        memSize = int(vm.conf['memSize'])
        self._wait = (delay_per_gib * max(memSize, 2048) + 1023) / 1024

        self.daemon = True

    def run(self):
        self._vm.log.debug('migration downtime thread started (%i steps)',
                           self._steps)

        if self._steps > 1:
            self._set_downtime_by_steps(self._downtime)
        else:
            self._set_downtime(self._downtime)

        self._vm.log.debug('migration downtime thread exiting')

    def stop(self):
        self._vm.log.debug('stopping migration downtime thread')
        self._stop.set()

    def _set_downtime_by_steps(self, max_downtime):
        for downtime in exponential_downtime(max_downtime, self._steps):
            if self._stop.isSet():
                break

            self._set_downtime(downtime)

            self._stop.wait(self._wait / self._steps)

    def _set_downtime(self, downtime):
        self._vm.log.debug('setting migration downtime to %d', downtime)
        self._vm._dom.migrateSetMaxDowntime(downtime, 0)


class MonitorThread(threading.Thread):
    _MIGRATION_MONITOR_INTERVAL = config.getint(
        'vars', 'migration_monitor_interval')  # seconds

    def __init__(self, vm, startTime):
        super(MonitorThread, self).__init__()
        self._stop = threading.Event()
        self._vm = vm
        self._startTime = startTime
        self.daemon = True
        self.progress = 0

    @property
    def enabled(self):
        return MonitorThread._MIGRATION_MONITOR_INTERVAL > 0

    def run(self):
        if self.enabled:
            self.monitor_migration()
        else:
            self._vm.log.info('migration monitor thread disabled'
                              ' (monitoring interval set to 0)')

    def monitor_migration(self):
        def update_progress(remaining, total):
            if remaining == 0 and total:
                return 100
            progress = 100 - 100 * remaining / total if total else 0
            return progress if (progress < 100) else 99

        self._vm.log.debug('starting migration monitor thread')

        memSize = int(self._vm.conf['memSize'])
        maxTimePerGiB = config.getint('vars',
                                      'migration_max_time_per_gib_mem')
        migrationMaxTime = (maxTimePerGiB * memSize + 1023) / 1024
        lastProgressTime = time.time()
        lowmark = None
        progress_timeout = config.getint('vars', 'migration_progress_timeout')

        while not self._stop.isSet():
            self._stop.wait(self._MIGRATION_MONITOR_INTERVAL)
            (jobType, timeElapsed, _,
             dataTotal, dataProcessed, dataRemaining,
             memTotal, memProcessed, memRemaining,
             fileTotal, fileProcessed, _) = self._vm._dom.jobInfo()
            # from libvirt sources: data* = file* + mem*.
            # docs can be misleading due to misaligned lines.
            abort = False
            now = time.time()
            if 0 < migrationMaxTime < now - self._startTime:
                self._vm.log.warn('The migration took %d seconds which is '
                                  'exceeding the configured maximum time '
                                  'for migrations of %d seconds. The '
                                  'migration will be aborted.',
                                  now - self._startTime,
                                  migrationMaxTime)
                abort = True
            elif (lowmark is None) or (lowmark > dataRemaining):
                lowmark = dataRemaining
                lastProgressTime = now
            elif (now - lastProgressTime) > progress_timeout:
                # Migration is stuck, abort
                self._vm.log.warn(
                    'Migration is stuck: Hasn\'t progressed in %s seconds. '
                    'Aborting.' % (now - lastProgressTime))
                abort = True

            if abort:
                self._vm._dom.abortJob()
                self.stop()
                break

            if dataRemaining > lowmark:
                self._vm.log.warn(
                    'Migration stalling: remaining (%sMiB)'
                    ' > lowmark (%sMiB).'
                    ' Refer to RHBZ#919201.',
                    dataRemaining / Mbytes, lowmark / Mbytes)

            if jobType != libvirt.VIR_DOMAIN_JOB_NONE:
                self.progress = update_progress(dataRemaining, dataTotal)

                self._vm.log.info('Migration Progress: %s seconds elapsed,'
                                  ' %s%% of data processed' %
                                  (timeElapsed / 1000, self.progress))

    def stop(self):
        self._vm.log.debug('stopping migration monitor thread')
        self._stop.set()
