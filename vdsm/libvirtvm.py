#
# Copyright 2009-2011 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import traceback
import libvirt
import libvirt_qemu
import xml.dom.minidom
import time
import threading
import json

import vm
from define import ERROR, doneCode, errCode
import utils
import constants
import guestIF
import libvirtev
import libvirtconnection
from config import config
import hooks
import caps
import configNetwork

_VMCHANNEL_DEVICE_NAME = 'com.redhat.rhevm.vdsm'

class VmStatsThread(utils.AdvancedStatsThread):
    MBPS_TO_BPS = 10**6 / 8

    def __init__(self, vm):
        utils.AdvancedStatsThread.__init__(self, log=vm.log, daemon=True)
        self._vm = vm

        self.highWrite = utils.AdvancedStatsFunction(self._highWrite,
                             config.getint('vars', 'vm_watermark_interval'))
        self.updateVolumes = utils.AdvancedStatsFunction(self._updateVolumes,
                             config.getint('irs', 'vol_size_sample_interval'))

        self.sampleCpu = utils.AdvancedStatsFunction(self._sampleCpu,
                             config.getint('vars', 'vm_sample_cpu_interval'),
                             config.getint('vars', 'vm_sample_cpu_window'))
        self.sampleDisk = utils.AdvancedStatsFunction(self._sampleDisk,
                             config.getint('vars', 'vm_sample_disk_interval'),
                             config.getint('vars', 'vm_sample_disk_window'))
        self.sampleDiskLatency = utils.AdvancedStatsFunction(self._sampleDiskLatency,
                             config.getint('vars', 'vm_sample_disk_latency_interval'),
                             config.getint('vars', 'vm_sample_disk_latency_window'))
        self.sampleNet = utils.AdvancedStatsFunction(self._sampleNet,
                             config.getint('vars', 'vm_sample_net_interval'),
                             config.getint('vars', 'vm_sample_net_window'))

        self.addStatsFunction(self.highWrite, self.updateVolumes, self.sampleCpu,
                              self.sampleDisk, self.sampleDiskLatency, self.sampleNet)

    def _highWrite(self):
        if not self._vm._volumesPrepared:
            # Avoid queries from storage during recovery process
            return

        for vmDrive in self._vm._devices[vm.DISK_DEVICES]:
            if vmDrive.blockDev and vmDrive.format == 'cow':
                capacity, alloc, physical = \
                                        self._vm._dom.blockInfo(vmDrive.path, 0)
                if physical - alloc < self._vm._MIN_DISK_REMAIN:
                    self._log.info('%s/%s apparent: %s capacity: %s, alloc: %s phys: %s',
                                  vmDrive.domainID, vmDrive.volumeID,
                                  vmDrive.apparentsize, capacity, alloc, physical)
                    self._vm._onHighWrite(vmDrive.name, alloc)

    def _updateVolumes(self):
        if not self._vm._volumesPrepared:
            # Avoid queries from storage during recovery process
            return

        for vmDrive in self._vm._devices[vm.DISK_DEVICES]:
            if not vmDrive.isVdsmImage():
                continue
            volSize = self._vm.cif.irs.getVolumeSize(vmDrive.domainID,
                      vmDrive.poolID, vmDrive.imageID, vmDrive.volumeID)
            if volSize['status']['code'] == 0 and not vmDrive.needExtend:
                vmDrive.truesize = int(volSize['truesize'])
                vmDrive.apparentsize = int(volSize['apparentsize'])

    def _sampleCpu(self):
        state, maxMem, memory, nrVirtCpu, cpuTime = self._vm._dom.info()
        return cpuTime / 1000**3

    def _sampleDisk(self):
        if not self._vm._volumesPrepared:
            # Avoid queries from storage during recovery process
            return

        diskSamples = {}
        for vmDrive in self._vm._devices[vm.DISK_DEVICES]:
            diskSamples[vmDrive.name] = self._vm._dom.blockStats(vmDrive.name)

        return diskSamples

    def _sampleDiskLatency(self):
        if not self._vm._volumesPrepared:
            # Avoid queries from storage during recovery process
            return

        def _blockstatsParses(devList):
            # The json output looks like:
            # {u'return': [{u'device': u'drive-ide0-0-0',
            #               u'stats': {u'rd_operations': 0, u'flush_total_time_ns': 0, u'wr_highest_offset': 0, u'rd_total_time_ns': 0,
            #                          u'rd_bytes': 0, u'wr_total_time_ns': 0, u'flush_operations': 0, u'wr_operations': 0, u'wr_bytes':0},
            #               u'parent': {u'stats': {u'rd_operations': 0, u'flush_total_time_ns': 0, u'wr_highest_offset': 0,
            #                                      u'rd_total_time_ns': 0, u'rd_bytes': 0, u'wr_total_time_ns': 0, u'flush_operations': 0,
            #                                      u'wr_operations': 0, u'wr_bytes': 0}
            #                          }
            #               },
            #               {u'device': u'drive-ide0-1-0',
            #                u'stats': {u'rd_operations': 0, u'flush_total_time_ns': 0, u'wr_highest_offset': 0, u'rd_total_time_ns': 0,
            #                           u'rd_bytes': 0, u'wr_total_time_ns': 0, u'flush_operations': 0, u'wr_operations': 0, u'wr_bytes': 0}
            #               }],
            #  u'id': u'libvirt-9'}
            stats = {}
            for item in devList['return']:
                fullDevName = item['device']
                alias = fullDevName[len('drive-'):].strip()
                devStats = item['stats']
                stats[alias] = {'rd_op':devStats['rd_operations'],
                                'wr_op':devStats['wr_operations'],
                                'flush_op':devStats['flush_operations'],
                                'rd_total_time_ns':devStats['rd_total_time_ns'],
                                'wr_total_time_ns':devStats['wr_total_time_ns'],
                                'flush_total_time_ns':devStats['flush_total_time_ns']}

            return stats

        diskLatency = {}
        cmd = json.dumps({ "execute" : "query-blockstats" })
        res = libvirt_qemu.qemuMonitorCommand(self._vm._dom, cmd,
                            libvirt_qemu.VIR_DOMAIN_QEMU_MONITOR_COMMAND_DEFAULT)
        out = json.loads(res)

        stats = _blockstatsParses(out)
        for vmDrive in self._vm._devices[vm.DISK_DEVICES]:
            try:
                diskLatency[vmDrive.name] = stats[vmDrive.alias]
            except KeyError:
                diskLatency[vmDrive.name] = {'rd_op':0, 'wr_op':0, 'flush_op':0,
                                             'rd_total_time_ns':0,
                                             'wr_total_time_ns':0,
                                             'flush_total_time_ns':0}
                self._log.warn("Disk %s latency not available", vmDrive.name)

        return diskLatency

    def _sampleNet(self):
        netSamples = {}
        for nic in self._vm._devices[vm.NIC_DEVICES]:
            netSamples[nic.name] = self._vm._dom.interfaceStats(nic.name)
        return netSamples

    def _getCpuStats(self, stats):
        stats['cpuSys'] = 0.0
        sInfo, eInfo, sampleInterval = self.sampleCpu.getStats()

        try:
            stats['cpuUser'] = 100.0 * (eInfo - sInfo) / sampleInterval
        except (TypeError, ZeroDivisionError):
            self._log.debug("CPU stats not available")
            stats['cpuUser'] = 0.0

        stats['cpuIdle'] = max(0.0, 100.0 - stats['cpuUser'])

    def _getNetworkStats(self, stats):
        stats['network'] = {}
        sInfo, eInfo, sampleInterval = self.sampleNet.getStats()

        for nic in self._vm._devices[vm.NIC_DEVICES]:
            ifSpeed = [100, 1000][nic.nicModel in ('e1000', 'virtio')]

            ifStats = {'macAddr':   nic.macAddr,
                       'name':      nic.name,
                       'speed':     str(ifSpeed),
                       'state':     'unknown'}

            try:
                ifStats['rxErrors']  = str(eInfo[nic.name][2])
                ifStats['rxDropped'] = str(eInfo[nic.name][3])
                ifStats['txErrors']  = str(eInfo[nic.name][6])
                ifStats['txDropped'] = str(eInfo[nic.name][7])

                ifRxBytes = (100.0 * (eInfo[nic.name][0] - sInfo[nic.name][0])
                             / sampleInterval / ifSpeed / self.MBPS_TO_BPS)
                ifTxBytes = (100.0 * (eInfo[nic.name][4] - sInfo[nic.name][4])
                             / sampleInterval / ifSpeed / self.MBPS_TO_BPS)

                ifStats['rxRate'] = '%.1f' % ifRxBytes
                ifStats['txRate'] = '%.1f' % ifTxBytes
            except (KeyError, TypeError, ZeroDivisionError):
                self._log.debug("Network stats not available")

            stats['network'][nic.name] = ifStats

    def _getDiskStats(self, stats):
        sInfo, eInfo, sampleInterval = self.sampleDisk.getStats()

        for vmDrive in self._vm._devices[vm.DISK_DEVICES]:
            dName = vmDrive.name
            dStats = {}
            try:
                dStats = {'truesize':     str(vmDrive.truesize),
                          'apparentsize': str(vmDrive.apparentsize),
                          'imageID':      vmDrive.imageID}

                dStats['readRate'] = ((eInfo[dName][1] - sInfo[dName][1])
                                      / sampleInterval)
                dStats['writeRate'] = ((eInfo[dName][3] - sInfo[dName][3])
                                       / sampleInterval)
            except (AttributeError, KeyError, TypeError, ZeroDivisionError):
                self._log.debug("Disk %s stats not available", dName)

            stats[dName] = dStats

    def _getDiskLatency(self, stats):
        sInfo, eInfo, sampleInterval = self.sampleDiskLatency.getStats()

        def _avgLatencyCalc(sData, eData):
            readLatency = 0 if not (eData['rd_op'] - sData['rd_op']) \
                            else (eData['rd_total_time_ns'] - sData['rd_total_time_ns']) / \
                                 (eData['rd_op'] - sData['rd_op'])
            writeLatency = 0 if not (eData['wr_op'] - sData['wr_op']) \
                            else (eData['wr_total_time_ns'] - sData['wr_total_time_ns']) / \
                                 (eData['wr_op'] - sData['wr_op'])
            flushLatency = 0 if not (eData['flush_op'] - sData['flush_op']) \
                            else (eData['flush_total_time_ns'] - sData['flush_total_time_ns']) / \
                                 (eData['flush_op'] - sData['flush_op'])

            return str(readLatency), str(writeLatency), str(flushLatency)

        for vmDrive in self._vm._devices[vm.DISK_DEVICES]:
            dName = vmDrive.name
            dLatency = {'readLatency':  '0',
                        'writeLatency': '0',
                        'flushLatency': '0'}
            try:
                dLatency['readLatency'], dLatency['writeLatency'], \
                dLatency['flushLatency'] = _avgLatencyCalc(sInfo[dName], eInfo[dName])
            except (KeyError, TypeError):
                self._log.debug("Disk %s latency not available", dName)
            else:
                stats[dName].update(dLatency)

    def get(self):
        stats = {}

        try:
            stats['statsAge'] = time.time() - self.getLastSampleTime()
        except TypeError:
            self._log.debug("Stats age not available")
            stats['statsAge'] = -1.0

        self._getCpuStats(stats)
        self._getNetworkStats(stats)
        self._getDiskStats(stats)
        self._getDiskLatency(stats)

        return stats

    def handleStatsException(self, ex):
        # We currently handle only libvirt exceptions
        if not hasattr(ex, "get_error_code"):
            return False

        # We currently handle only the missing domain exception
        if ex.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN:
            return False

        # If a VM is down, hibernating, migrating, destroyed or in the
        # process of being shutdown we were expecting it to disappear
        if ((self._vm.lastStatus in ('Down',
                                     'Saving State', 'Migration Source'))
                or self._vm.destroyed
                or self._vm._guestEvent == 'Powering down'):
            return True

        self._log.debug("VM not found, moving to Down", exc_info=True)
        self._vm.setDownStatus(ERROR, str(ex))

        return True

class MigrationDowntimeThread(threading.Thread):
    def __init__(self, vm, downtime, wait):
        super(MigrationDowntimeThread, self).__init__()
        self.DOWNTIME_STEPS = config.getint('vars', 'migration_downtime_steps')

        self._vm = vm
        self._downtime = downtime
        self._wait = wait
        self._stop = threading.Event()

        self.daemon = True
        self.start()

    def run(self):
        self._vm.log.debug('migration downtime thread started')

        for i in range(self.DOWNTIME_STEPS):
            self._stop.wait(self._wait / self.DOWNTIME_STEPS)

            if self._stop.isSet():
                break

            downtime = self._downtime * (i + 1) / self.DOWNTIME_STEPS
            self._vm.log.debug('setting migration downtime to %d', downtime)
            self._vm._dom.migrateSetMaxDowntime(downtime, 0)

        self._vm.log.debug('migration downtime thread exiting')

    def cancel(self):
        self._vm.log.debug('canceling migration downtime thread')
        self._stop.set()

class MigrationMonitorThread(threading.Thread):
    _MIGRATION_MONITOR_INTERVAL = config.getint('vars', 'migration_monitor_interval')   # seconds

    def __init__(self, vm):
        super(MigrationMonitorThread, self).__init__()
        self._stop = threading.Event()
        self._vm = vm
        self.daemon = True

    def run(self):
        self._vm.log.debug('starting migration monitor thread')

        lastProgressTime = time.time()
        smallest_dataRemaining = None

        while not self._stop.isSet():
            self._stop.wait(self._MIGRATION_MONITOR_INTERVAL)
            jobType, timeElapsed, _,     \
            dataTotal, dataProcessed, dataRemaining, \
            memTotal, memProcessed, _,   \
            fileTotal, fileProcessed, _ = self._vm._dom.jobInfo()

            if smallest_dataRemaining is None or smallest_dataRemaining > dataRemaining:
                smallest_dataRemaining = dataRemaining
                lastProgressTime = time.time()
            elif time.time() - lastProgressTime > config.getint('vars', 'migration_timeout'):
                # Migration is stuck, abort
                self._vm.log.warn(
                        'Migration is stuck: Hasn\'t progressed in %s seconds. Aborting.' % (time.time() - lastProgressTime)
                    )
                self._vm._dom.abortJob()
                self.stop()
                break

            if jobType == 0:
                continue

            dataProgress = 100*dataProcessed / dataTotal if dataTotal else 0
            memProgress = 100*memProcessed / memTotal if memTotal else 0

            self._vm.log.info(
                    'Migration Progress: %s seconds elapsed, %s%% of data processed, %s%% of mem processed'
                    % (timeElapsed/1000,dataProgress,memProgress)
                )


    def stop(self):
        self._vm.log.debug('stopping migration monitor thread')
        self._stop.set()

class MigrationSourceThread(vm.MigrationSourceThread):

    def _setupRemoteMachineParams(self):
        vm.MigrationSourceThread._setupRemoteMachineParams(self)
        if self._mode != 'file':
            self._machineParams['migrationDest'] = 'libvirt'
        self._machineParams['_srcDomXML'] = self._vm._dom.XMLDesc(0)

    def _startUnderlyingMigration(self):
        self._preparingMigrationEvt = True
        if self._mode == 'file':
            hooks.before_vm_hibernate(self._vm._dom.XMLDesc(0), self._vm.conf)
            try:
                self._vm._vmStats.pause()
                fname = self._vm.cif.prepareVolumePath(self._dst)
                try:
                    self._vm._dom.save(fname)
                finally:
                    self._vm.cif.teardownVolumePath(self._dst)
            except:
                self._vm._vmStats.cont()
                raise
        else:
            hooks.before_vm_migrate_source(self._vm._dom.XMLDesc(0), self._vm.conf)
            response = self.destServer.migrationCreate(self._machineParams)
            if response['status']['code']:
                self.status = response
                raise RuntimeError('migration destination error: ' + response['status']['message'])
            if config.getboolean('vars', 'ssl'):
                transport = 'tls'
            else:
                transport = 'tcp'
            duri = 'qemu+%s://%s/system' % (transport, self.remoteHost)
            muri = 'tcp://%s' % self.remoteHost
            self._vm.log.debug('starting migration to %s', duri)

            t = MigrationDowntimeThread(self._vm, int(self._downtime),
                                        self._vm._migrationTimeout() / 2)

            if MigrationMonitorThread._MIGRATION_MONITOR_INTERVAL:
                monitorThread = MigrationMonitorThread(self._vm)
                monitorThread.start()

            try:
                if 'qxl' in self._vm.conf['display'] and \
                   self._vm.conf.get('clientIp'):
                    SPICE_MIGRATION_HANDOVER_TIME = 120
                    self._vm._reviveTicket(SPICE_MIGRATION_HANDOVER_TIME)

                maxBandwidth = config.getint('vars', 'migration_max_bandwidth')
                #FIXME: there still a race here with libvirt,
                # if we call stop() and libvirt migrateToURI2 didn't start
                # we may return migration stop but it will start at libvirt
                # side
                self._preparingMigrationEvt = False
                if not self._migrationCanceledEvt:
                    self._vm._dom.migrateToURI2(duri, muri, None,
                        libvirt.VIR_MIGRATE_LIVE | libvirt.VIR_MIGRATE_PEER2PEER,
                        None, maxBandwidth)
            finally:
                t.cancel()
                if MigrationMonitorThread._MIGRATION_MONITOR_INTERVAL:
                    monitorThread.stop()

    def stop(self):
        # if its locks we are before the migrateToURI2()
        # call so no need to abortJob()
        try:
            self._migrationCanceledEvt = True
            self._vm._dom.abortJob()
        except libvirt.libvirtError, e:
            # TODO: yes its not nice searching in the error message,
            # but this is the libvrirt solution
            # this will be solved at bz #760149
            if e.get_error_code() == libvirt.VIR_ERR_OPERATION_FAILED and \
                    'canceled by client' in e.get_error_message():
                    # this is exception that libvirt raise when calling
                    # abortJob()
                    raise
            elif not self._preparingMigrationEvt:
                    raise

class TimeoutError(libvirt.libvirtError): pass

class NotifyingVirDomain:
    # virDomain wrapper that notifies vm when a method raises an exception with
    # get_error_code() = VIR_ERR_OPERATION_TIMEOUT

    def __init__(self, dom, tocb):
        self._dom = dom
        self._cb = tocb

    def __getattr__(self, name):
        attr = getattr(self._dom, name)
        if not callable(attr):
            return attr
        def f(*args, **kwargs):
            try:
                ret = attr(*args, **kwargs)
                self._cb(False)
                return ret
            except libvirt.libvirtError, e:
                if e.get_error_code() == libvirt.VIR_ERR_OPERATION_TIMEOUT:
                    self._cb(True)
                    toe = TimeoutError(e.get_error_message())
                    toe.err = e.err
                    raise toe
                raise
        return f


class _DomXML:
    def __init__(self, conf, log):
        """
        Create the skeleton of a libvirt domain xml

        <domain type="kvm">
            <name>vmName</name>
            <uuid>9ffe28b6-6134-4b1e-8804-1185f49c436f</uuid>
            <memory>262144</memory>
            <currentMemory>262144</currentMemory>
            <vcpu>smp</vcpu>
            <devices>
            </devices>
        </domain>

        """
        self.conf = conf
        self.log = log

        self.doc = xml.dom.minidom.Document()
        self.dom = self.doc.createElement('domain')

        if utils.tobool(self.conf.get('kvmEnable', 'true')):
            self.dom.setAttribute('type', 'kvm')
        else:
            self.dom.setAttribute('type', 'qemu')

        self.doc.appendChild(self.dom)

        self.dom.appendChild(self.doc.createElement('name')) \
           .appendChild(self.doc.createTextNode(self.conf['vmName']))
        self.dom.appendChild(self.doc.createElement('uuid')) \
           .appendChild(self.doc.createTextNode(self.conf['vmId']))
        memSizeKB = str(int(self.conf.get('memSize', '256')) * 1024)
        self.dom.appendChild(self.doc.createElement('memory')) \
           .appendChild(self.doc.createTextNode(memSizeKB))
        self.dom.appendChild(self.doc.createElement('currentMemory')) \
           .appendChild(self.doc.createTextNode(memSizeKB))
        self.dom.appendChild(self.doc.createElement('vcpu')) \
           .appendChild(self.doc.createTextNode(self.conf['smp']))

        self._devices = self.doc.createElement('devices')
        self.dom.appendChild(self._devices)

    def appendConsole(self):
        """
        Add <console> elelemt to domain

        <console type='pty'>
           <target type='virtio' port='0'/>
        </console>
        """
        m = self.doc.createElement('console')
        m.setAttribute('type', 'pty')
        t = self.doc.createElement('target')
        t.setAttribute('port', '0')
        t.setAttribute('type', 'virtio')
        m.appendChild(t)
        self._devices.appendChild(m)

    def appendClock(self):
        """
        Add <clock> element to domain:

        <clock offset="variable" adjustment="-3600">
            <timer name="rtc" tickpolicy="catchup">
        </clock>
        """

        m = self.doc.createElement('clock')
        m.setAttribute('offset', 'variable')
        m.setAttribute('adjustment', str(self.conf.get('timeOffset', 0)))

        if utils.tobool(self.conf.get('tdf', True)):
            t = self.doc.createElement('timer')
            t.setAttribute('name', 'rtc')
            t.setAttribute('tickpolicy', 'catchup')
            m.appendChild(t)

        self.dom.appendChild(m)

    def appendOs(self):
        """
        Add <os> element to domain:

        <os>
            <type arch="x86_64" machine="pc">hvm</type>
            <boot dev="cdrom"/>
            <kernel>/tmp/vmlinuz-2.6.18</kernel>
            <initrd>/tmp/initrd-2.6.18.img</initrd>
            <cmdline>ARGs 1</cmdline>
            <smbios mode="sysinfo"/>
        </os>
        """

        oselem = self.doc.createElement('os')
        self.dom.appendChild(oselem)
        typeelem = self.doc.createElement('type')
        oselem.appendChild(typeelem)
        typeelem.setAttribute('arch', 'x86_64')
        typeelem.setAttribute('machine', self.conf.get('emulatedMachine', 'pc'))
        typeelem.appendChild(self.doc.createTextNode('hvm'))

        qemu2libvirtBoot = {'a': 'fd', 'c': 'hd', 'd': 'cdrom', 'n': 'network'}
        for c in self.conf.get('boot', ''):
            m = self.doc.createElement('boot')
            m.setAttribute('dev', qemu2libvirtBoot[c])
            oselem.appendChild(m)

        if self.conf.get('initrd'):
            m = self.doc.createElement('initrd')
            m.appendChild(self.doc.createTextNode(self.conf['initrd']))
            oselem.appendChild(m)

        if self.conf.get('kernel'):
            m = self.doc.createElement('kernel')
            m.appendChild(self.doc.createTextNode(self.conf['kernel']))
            oselem.appendChild(m)

        if self.conf.get('kernelArgs'):
            m = self.doc.createElement('cmdline')
            m.appendChild(self.doc.createTextNode(self.conf['kernelArgs']))
            oselem.appendChild(m)

        m = self.doc.createElement('smbios')
        m.setAttribute('mode', 'sysinfo')
        oselem.appendChild(m)

    def appendSysinfo(self, osname, osversion, hostUUID):
        """
        Add <sysinfo> element to domain:

        <sysinfo type="smbios">
          <bios>
            <entry name="vendor">QEmu/KVM</entry>
            <entry name="version">0.13</entry>
          </bios>
          <system>
            <entry name="manufacturer">Fedora</entry>
            <entry name="product">Virt-Manager</entry>
            <entry name="version">0.8.2-3.fc14</entry>
            <entry name="serial">32dfcb37-5af1-552b-357c-be8c3aa38310</entry>
            <entry name="uuid">c7a5fdbd-edaf-9455-926a-d65c16db1809</entry>
          </system>
        </sysinfo>
        """

        sysinfoelem = self.doc.createElement('sysinfo')
        sysinfoelem.setAttribute('type', 'smbios')
        self.dom.appendChild(sysinfoelem)

        syselem = self.doc.createElement('system')
        sysinfoelem.appendChild(syselem)

        def appendEntry(k, v):
            m = self.doc.createElement('entry')
            m.setAttribute('name', k)
            m.appendChild(self.doc.createTextNode(v))
            syselem.appendChild(m)

        appendEntry('manufacturer', 'Red Hat')
        appendEntry('product', osname)
        appendEntry('version', osversion)
        appendEntry('serial', hostUUID)
        appendEntry('uuid', self.conf['vmId'])

    def appendFeatures(self):
        """
        Add machine features to domain xml.

        Currently only
        <features>
            <acpi/>
        <features/>
        """
        if utils.tobool(self.conf.get('acpiEnable', 'true')):
            self.dom.appendChild(self.doc.createElement('features')) \
               .appendChild(self.doc.createElement('acpi'))

    def appendCpu(self):
        """
        Add guest CPU definition.

        <cpu match="exact">
            <model>qemu64</model>
            <topology sockets="S" cores="C" threads="T"/>
            <feature policy="require" name="sse2"/>
            <feature policy="disable" name="svm"/>
        </cpu>
        """

        features = self.conf.get('cpuType', 'qemu64').split(',')
        model = features[0]
        cpu = self.doc.createElement('cpu')
        cpu.setAttribute('match', 'exact')
        m = self.doc.createElement('model')
        m.appendChild(self.doc.createTextNode(model))
        cpu.appendChild(m)
        if 'smpCoresPerSocket' in self.conf or 'smpThreadsPerCore' in self.conf:
            topo = self.doc.createElement('topology')
            vcpus = int(self.conf.get('smp', '1'))
            cores = int(self.conf.get('smpCoresPerSocket', '1'))
            threads = int(self.conf.get('smpThreadsPerCore', '1'))
            topo.setAttribute('sockets', str(vcpus / cores / threads))
            topo.setAttribute('cores', str(cores))
            topo.setAttribute('threads', str(threads))
            cpu.appendChild(topo)

        # This hack is for backward compatibility as the libvirt does not allow
        # 'qemu64' guest on intel hardware
        if model == 'qemu64' and not '+svm' in features:
            features += ['-svm']

        for feature in features[1:]:
            # convert Linux name of feature to libvirt
            if feature[1:5] == 'sse4_':
                feature = feature[0] + 'sse4.' + feature[6:]

            f = self.doc.createElement('feature')
            if feature[0] == '+':
                f.setAttribute('policy', 'require')
                f.setAttribute('name', feature[1:])
            elif feature[0] == '-':
                f.setAttribute('policy', 'disable')
                f.setAttribute('name', feature[1:])
            cpu.appendChild(f)
        self.dom.appendChild(cpu)

    def _appendBalloon(self):
        """Add balloon device. Currently unsupported by RHEV-M"""
        m = self.doc.createElement('memballoon')
        m.setAttribute('model', 'none')
        self._devices.appendChild(m)

    def _appendAgentDevice(self, path):
        """
          <channel type='unix'>
             <target type='virtio' name='org.linux-kvm.port.0'/>
             <source mode='bind' path='/tmp/socket'/>
          </channel>
        """
        channel = self.doc.createElement('channel')
        channel.setAttribute('type', 'unix')
        target = xml.dom.minidom.Element('target')
        target.setAttribute('type', 'virtio')
        target.setAttribute('name', _VMCHANNEL_DEVICE_NAME)
        source = xml.dom.minidom.Element('source')
        source.setAttribute('mode', 'bind')
        source.setAttribute('path', path)
        channel.appendChild(target)
        channel.appendChild(source)
        self._devices.appendChild(channel)

    def appendInput(self):
        """
        Add input device.

        <input bus="ps2" type="mouse"/>
        """
        input = self.doc.createElement('input')
        if utils.tobool(self.conf.get('tabletEnable')):
            input.setAttribute('type', 'tablet')
            input.setAttribute('bus', 'usb')
        else:
            input.setAttribute('type', 'mouse')
            input.setAttribute('bus', 'ps2')
        self._devices.appendChild(input)

    def appendGraphics(self):
        """
        Add graphics section to domain xml.

        <graphics autoport="yes" listen="0" type="vnc"/>

        or

        <graphics autoport="yes" keymap="en-us" listen="0" port="5910"
                  tlsPort="5890" type="spice" passwd="foo"
                  passwdValidTo="2010-04-09T15:51:00"/>
        <channel type='spicevmc'>
           <target type='virtio' name='com.redhat.spice.0'/>
         </channel>
        """
        graphics = self.doc.createElement('graphics')
        if self.conf['display'] == 'vnc':
            graphics.setAttribute('type', 'vnc')
            graphics.setAttribute('port', self.conf['displayPort'])
            graphics.setAttribute('autoport', 'yes')
        elif 'qxl' in self.conf['display']:
            graphics.setAttribute('type', 'spice')
            graphics.setAttribute('port', self.conf['displayPort'])
            graphics.setAttribute('tlsPort', self.conf['displaySecurePort'])
            graphics.setAttribute('autoport', 'yes')
            if self.conf.get('spiceSecureChannels'):
                for channel in self.conf['spiceSecureChannels'].split(','):
                    m = self.doc.createElement('channel')
                    m.setAttribute('name', channel[1:])
                    m.setAttribute('mode', 'secure')
                    graphics.appendChild(m)

            vmc = self.doc.createElement('channel')
            vmc.setAttribute('type', 'spicevmc')
            m = self.doc.createElement('target')
            m.setAttribute('type', 'virtio')
            m.setAttribute('name', 'com.redhat.spice.0')
            vmc.appendChild(m)
            self._devices.appendChild(vmc)

        if self.conf.get('displayNetwork'):
            listen = self.doc.createElement('listen')
            listen.setAttribute('type', 'network')
            listen.setAttribute('network', configNetwork.NETPREFIX +
                self.conf.get('displayNetwork'))
            graphics.appendChild(listen)
        else:
            graphics.setAttribute('listen', '0')

        if self.conf.get('keyboardLayout'):
            graphics.setAttribute('keymap', self.conf['keyboardLayout'])
        if not 'spiceDisableTicketing' in self.conf:
            graphics.setAttribute('passwd', '*****')
            graphics.setAttribute('passwdValidTo', '1970-01-01T00:00:01')
        self._devices.appendChild(graphics)

    def toxml(self):
        return self.doc.toprettyxml(encoding='utf-8')


class GeneralDevice(vm.Device):
    def __init__(self, conf, log, **kwargs):
        vm.Device.__init__(self, conf, log, **kwargs)

    def getXML(self):
        """
        Create domxml for general device
        """
        doc = xml.dom.minidom.Document()
        dev = doc.createElement(self.type)
        if self.device:
            dev.setAttribute('type', self.device)
        if hasattr(self, 'address'):
            address = doc.createElement('address')
            for key, value in self.address.iteritems():
                address.setAttribute(key, value)
            dev.appendChild(address)

        return dev

class ControllerDevice(vm.Device):
    def __init__(self, conf, log, **kwargs):
        vm.Device.__init__(self, conf, log, **kwargs)

    def getXML(self):
        """
        Create domxml for controller device
        """
        doc = xml.dom.minidom.Document()
        ctrl = doc.createElement('controller')
        ctrl.setAttribute('type', self.device)
        if self.device == 'virtio-serial':
            ctrl.setAttribute('index', '0')
            ctrl.setAttribute('ports', '16')
        if hasattr(self, 'address'):
            address = doc.createElement('address')
            for key, value in self.address.iteritems():
                address.setAttribute(key, value)
            ctrl.appendChild(address)

        return ctrl

class VideoDevice(vm.Device):
    def __init__(self, conf, log, **kwargs):
        vm.Device.__init__(self, conf, log, **kwargs)

    def getXML(self):
        """
        Create domxml for video device
        """
        doc = xml.dom.minidom.Document()
        video = doc.createElement('video')
        m = doc.createElement('model')
        m.setAttribute('type', self.device)
        m.setAttribute('vram', self.specParams['vram'])
        m.setAttribute('heads', '1')
        video.appendChild(m)
        if hasattr(self, 'address'):
            address = doc.createElement('address')
            for key, value in self.address.iteritems():
                address.setAttribute(key, value)
            video.appendChild(address)

        return video

class SoundDevice(vm.Device):
    def __init__(self, conf, log, **kwargs):
        vm.Device.__init__(self, conf, log, **kwargs)

    def getXML(self):
        """
        Create domxml for sound device
        """
        doc = xml.dom.minidom.Document()
        sound = doc.createElement('sound')
        sound.setAttribute('model', self.device)
        if hasattr(self, 'address'):
            address = doc.createElement('address')
            for key, value in self.address.iteritems():
                address.setAttribute(key, value)
            sound.appendChild(address)

        return sound

class NetworkInterfaceDevice(vm.Device):
    def __init__(self, conf, log, **kwargs):
        vm.Device.__init__(self, conf, log, **kwargs)
        self.sndbufParam = False
        self._customize()

    def _customize(self):
        # Customize network device
        vhosts = self._getVHostSettings()
        self.driver = vhosts.get(self.network, False)
        try:
            self.sndbufParam = self.conf['custom']['sndbuf']
        except KeyError:
            pass    # custom_sndbuf not specified

    def _getVHostSettings(self):
        VHOST_MAP = {'true': 'vhost', 'false': 'qemu'}
        vhosts = {}
        vhostProp = self.conf.get('custom', {}).get('vhost', '')

        if vhostProp != '':
            for vhost in vhostProp.split(','):
                try:
                    vbridge, vstatus = vhost.split(':', 1)
                    vhosts[vbridge] = VHOST_MAP[vstatus.lower()]
                except (ValueError, KeyError):
                    self.log.warning("Unknown vhost format: %s", vhost)

        return vhosts

    def getXML(self):
        """
        Create domxml for network interface.

        <interface type="bridge">
            <mac address="aa:bb:dd:dd:aa:bb"/>
            <model type="virtio"/>
            <source bridge="engine"/>
            [<tune><sndbuf>0</sndbuf></tune>]
        </interface>
        """
        doc = xml.dom.minidom.Document()
        iface = doc.createElement('interface')
        iface.setAttribute('type', self.device)
        m = doc.createElement('mac')
        m.setAttribute('address', self.macAddr)
        iface.appendChild(m)
        m = doc.createElement('model')
        m.setAttribute('type', self.nicModel)
        iface.appendChild(m)
        m = doc.createElement('source')
        m.setAttribute('bridge', self.network)
        iface.appendChild(m)
        if hasattr(self, 'bootOrder'):
            bootOrder = doc.createElement('boot')
            bootOrder.setAttribute('order', self.bootOrder)
            iface.appendChild(bootOrder)
        if hasattr(self, 'address'):
            address = doc.createElement('address')
            for key, value in self.address.iteritems():
                address.setAttribute(key, value)
            iface.appendChild(address)
        if self.driver:
            m = doc.createElement('driver')
            m.setAttribute('name', self.driver)
            iface.appendChild(m)
        if self.sndbufParam:
            tune = doc.createElement('tune')
            sndbuf = doc.createElement('sndbuf')
            sndbuf.appendChild(doc.createTextNode(self.sndbufParam))
            tune.appendChild(sndbuf)
            iface.appendChild(tune)

        return iface

class Drive(vm.Device):
    def __init__(self, conf, log, **kwargs):
        if not kwargs.get('serial'):
            self.serial = kwargs.get('imageID'[-20:]) or ''
        vm.Device.__init__(self, conf, log, **kwargs)
        # Keep sizes as int
        self.reqsize = int(kwargs.get('reqsize', '0'))
        self.truesize = int(kwargs.get('truesize', '0'))
        self.apparentsize = int(kwargs.get('apparentsize', '0'))
        self.name = self._makeName()
        self._customize()

    def _customize(self):
        # Customize disk device
        if self.iface == 'virtio':
            try:
                self.cache = self.conf['custom']['viodiskcache']
            except KeyError:
                self.cache = config.get('vars', 'qemu_drive_cache')
        else:
            self.cache = config.get('vars', 'qemu_drive_cache')

    def _makeName(self):
        devname = {'ide': 'hd', 'virtio': 'vd', 'fdc': 'fd'}
        devindex = ''

        i = int(self.index)
        while i > 0:
            devindex = chr(ord('a') + (i % 26)) + devindex
            i /= 26

        return devname.get(self.iface, 'hd') + (devindex or 'a')

    def isVdsmImage(self):
        return getattr(self, 'poolID', False)

    def getXML(self):
        """
        Create domxml for disk/cdrom/floppy.

        <disk type='file' device='disk' snapshot='no'>
          <driver name='qemu' type='qcow2' cache='none'/>
          <source file='/path/to/image'/>
          <target dev='hda' bus='ide'/>
          <serial>54-a672-23e5b495a9ea</serial>
        </disk>
        """
        doc = xml.dom.minidom.Document()
        diskelem = doc.createElement('disk')
        device = getattr(self, 'device', 'disk')
        diskelem.setAttribute('device', device)
        source = doc.createElement('source')
        if self.blockDev:
            diskelem.setAttribute('type', 'block')
            source.setAttribute('dev', self.path)
        else:
            diskelem.setAttribute('type', 'file')
            source.setAttribute('file', self.path)
        diskelem.setAttribute('snapshot', 'no')
        diskelem.appendChild(source)
        target = doc.createElement('target')
        target.setAttribute('dev', self.name)
        if self.iface:
            target.setAttribute('bus', self.iface)
        diskelem.appendChild(target)
        if utils.tobool(self.readonly):
            readonly = doc.createElement('readonly')
            diskelem.appendChild(readonly)
        if hasattr(self, 'serial'):
            serial = doc.createElement('serial')
            serial.appendChild(doc.createTextNode(self.serial))
            diskelem.appendChild(serial)
        if hasattr(self, 'bootOrder'):
            bootOrder = doc.createElement('boot')
            bootOrder.setAttribute('order', self.bootOrder)
            diskelem.appendChild(bootOrder)
        if hasattr(self, 'address'):
            address = doc.createElement('address')
            for key, value in self.address.iteritems():
                address.setAttribute(key, value)
            diskelem.appendChild(address)
        if device == 'disk':
            driver = doc.createElement('driver')
            driver.setAttribute('name', 'qemu')
            if self.blockDev:
                driver.setAttribute('io', 'native')
            else:
                driver.setAttribute('io', 'threads')
            if self.format == 'cow':
                driver.setAttribute('type', 'qcow2')
            elif self.format:
                driver.setAttribute('type', 'raw')

            driver.setAttribute('cache', self.cache)

            if self.propagateErrors == 'on':
                driver.setAttribute('error_policy', 'enospace')
            else:
                driver.setAttribute('error_policy', 'stop')
            diskelem.appendChild(driver)
        elif device == 'floppy':
            if self.path and not utils.getUserPermissions(constants.QEMU_PROCESS_USER,
                                                          self.path)['write']:
                diskelem.appendChild(doc.createElement('readonly'))

        return diskelem

class LibvirtVm(vm.Vm):
    MigrationSourceThreadClass = MigrationSourceThread
    def __init__(self, cif, params):
        self._dom = None
        vm.Vm.__init__(self, cif, params)

        self._connection = libvirtconnection.get(cif)
        if 'vmName' not in self.conf:
            self.conf['vmName'] = 'n%s' % self.id
        self._guestSocektFile = constants.P_LIBVIRT_VMCHANNELS + \
                                self.conf['vmName'].encode('utf-8') + \
                                '.' + _VMCHANNEL_DEVICE_NAME
        # TODO find a better idea how to calculate this constant only after
        # config is initialized
        self._MIN_DISK_REMAIN = (100 -
                      config.getint('irs', 'volume_utilization_percent')) \
            * config.getint('irs', 'volume_utilization_chunk_mb') * 2**20 \
            / 100
        self._lastXMLDesc = '<domain><uuid>%s</uuid></domain>' % self.id
        self._devXmlHash = '0'
        self._released = False
        self._releaseLock = threading.Lock()
        self.saveState()


    def _buildCmdLine(self):
        domxml = _DomXML(self.conf, self.log)
        domxml.appendOs()

        osd = caps.osversion()
        domxml.appendSysinfo(
            osname=caps.OSName.RHEVH,
            osversion=osd.get('version', '') + '-' + osd.get('release', ''),
            hostUUID=utils.getHostUUID() )

        domxml.appendClock()
        domxml.appendFeatures()
        domxml.appendCpu()
        if utils.tobool(self.conf.get('vmchannel', 'true')):
            domxml._appendAgentDevice(self._guestSocektFile.decode('utf-8'))
        domxml._appendBalloon()
        domxml.appendInput()
        domxml.appendGraphics()
        domxml.appendConsole()

        for devType in self._devices:
            for dev in self._devices[devType]:
                devElem = dev.getXML()
                domxml._devices.appendChild(devElem)

        return domxml.toxml()

    def _initVmStats(self):
        self._vmStats = VmStatsThread(self)
        self._vmStats.start()
        self._guestEventTime = self._startTime

    def updateGuestCpuRunning(self):
        self._guestCpuRunning = self._dom.info()[0] == libvirt.VIR_DOMAIN_RUNNING

    def _getUnderlyingVmDevicesInfo(self):
        """
        Obtain underluing vm's devices info from libvirt.
        """
        self._getUnderlyingNetworkInterfaceInfo()
        self._getUnderlyingDriveInfo()
        self._getUnderlyingDisplayPort()
        self._getUnderlyingSoundDeviceInfo()
        self._getUnderlyingVideoDeviceInfo()
        self._getUnderlyingControllerDeviceInfo()
        # Obtain info of all uknown devices. Must be last!
        self._getUnderlyingUnknownDeviceInfo()

    def _domDependentInit(self):
        if self.destroyed:
            # reaching here means that Vm.destroy() was called before we could
            # handle it. We must handle it now
            try:
                self._dom.destroy()
            except:
                pass
            raise Exception('destroy() called before Vm started')

        self._getUnderlyingVmInfo()
        self._getUnderlyingVmDevicesInfo()

        # VmStatsThread may use block devices info from libvirt.
        # So, run it after you have this info
        self._initVmStats()
        self.guestAgent = guestIF.GuestAgent(self._guestSocektFile, self.log,
                   connect=utils.tobool(self.conf.get('vmchannel', 'true')))

        self._guestCpuRunning = self._dom.info()[0] == libvirt.VIR_DOMAIN_RUNNING
        if self.lastStatus not in ('Migration Destination',
                                   'Restoring state'):
            self._initTimePauseCode = self._readPauseCode(0)
        if 'recover' not in self.conf and self._initTimePauseCode:
            self.conf['pauseCode'] = self._initTimePauseCode
            if self._initTimePauseCode == 'ENOSPC':
                self.cont()
        self.conf['pid'] = self._getPid()

        nice = int(self.conf.get('nice', '0'))
        nice = max(min(nice, 19), 0)
        try:
            self._dom.setSchedulerParameters({'cpu_shares': (20 - nice) * 51})
        except:
            self.log.warning('failed to set Vm niceness', exc_info=True)

    def _run(self):
        self.log.info("VM wrapper has started")
        self.conf['smp'] = self.conf.get('smp', '1')

        if not 'recover' in self.conf:
            devices = self.buildConfDevices()
            self.preparePaths(devices[vm.DISK_DEVICES])
            # Update self.conf with updated devices
            # For old type vmParams, new 'devices' key will be
            # created with all devices info
            newDevices = []
            for dev in devices.values():
                newDevices.extend(dev)

            self.conf['devices'] = newDevices
            # We need to save conf here before we actually run VM.
            # It's not enough to save conf only on status changes as we did before,
            # because if vdsm will restarted between VM run and conf saving
            # we will fail in inconsistent state during recovery.
            # So, to get proper device objects during VM recovery flow
            # we must to have updated conf before VM run
            self.saveState()
        else:
            # TODO: In recover should loop over disks running on the VM because
            # conf may be outdated if something happened during restart.

            # For BC we should to keep running VM run after vdsm upgrade.
            # So, because this vm doesn't have normalize conf we need to build it
            # in recovery flow
            if not self.conf.get('devices'):
                devices = self.buildConfDevices()
            else:
                devices = self.getConfDevices()

        devMap = {vm.DISK_DEVICES: Drive, vm.NIC_DEVICES: NetworkInterfaceDevice,
                  vm.SOUND_DEVICES: SoundDevice, vm.VIDEO_DEVICES: VideoDevice,
                  vm.CONTROLLER_DEVICES: ControllerDevice, vm.GENERAL_DEVICES: GeneralDevice}

        for devType, devClass in devMap.items():
            for dev in devices[devType]:
                self._devices[devType].append(devClass(self.conf, self.log, **dev))

        # We should set this event as a last part of drives initialization
        self._pathsPreparedEvent.set()

        if self.conf.get('migrationDest'):
            return
        if not 'recover' in self.conf:
            domxml = hooks.before_vm_start(self._buildCmdLine(), self.conf)
            self.log.debug(domxml)
        if 'recover' in self.conf:
            self._dom = NotifyingVirDomain(
                            self._connection.lookupByUUIDString(self.id),
                            self._timeoutExperienced)
        elif 'restoreState' in self.conf:
            hooks.before_vm_dehibernate(self.conf.pop('_srcDomXML'), self.conf)

            fname = self.cif.prepareVolumePath(self.conf['restoreState'])
            try:
                self._connection.restore(fname)
            finally:
                self.cif.teardownVolumePath(self.conf['restoreState'])

            self._dom = NotifyingVirDomain(
                            self._connection.lookupByUUIDString(self.id),
                            self._timeoutExperienced)
        else:
            flags = libvirt.VIR_DOMAIN_NONE
            if 'launchPaused' in self.conf:
                flags |= libvirt.VIR_DOMAIN_START_PAUSED
                self.conf['pauseCode'] = 'NOERR'
                del self.conf['launchPaused']
            self._dom = NotifyingVirDomain(
                            self._connection.createXML(domxml, flags),
                            self._timeoutExperienced)
            if self._dom.UUIDString() != self.id:
                raise Exception('libvirt bug 603494')
            hooks.after_vm_start(self._dom.XMLDesc(0), self.conf)
        if not self._dom:
            self.setDownStatus(ERROR, 'failed to start libvirt vm')
            return
        self._domDependentInit()

    def hotplugDisk(self, params):
        diskParams = params.get('drive', {})
        diskParams['path'] = self.cif.prepareVolumePath(diskParams)
        if vm.isVdsmImage(diskParams):
            self._normalizeVdsmImg(diskParams)

        drive = Drive(self.conf, self.log, **diskParams)
        driveXml =  drive.getXML().toprettyxml(encoding='utf-8')
        self.log.debug("Hotplug disk xml: %s" % (driveXml))

        try:
            self._dom.attachDevice(driveXml)
        except libvirt.libvirtError, e:
            self.log.error("Hotplug failed", exc_info=True)
            self.cif.teardownVolumePath(diskParams)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return errCode['noVM']
            return {'status' : {'code': errCode['hotplugDisk']['status']['code'],
                                'message': e.message}}
        else:
            # FIXME!  We may have a problem here if vdsm dies right after
            # we sent command to libvirt and before save conf. In this case
            # we will gather almost all needed info about this drive from
            # the libvirt during recovery process.
            self._devices[vm.DISK_DEVICES].append(drive)
            self.conf['devices'].append(diskParams)
            self.saveState()
            self._getUnderlyingDriveInfo()

        return {'status': doneCode, 'vmList': self.cif.vmContainer[params['vmId']].status()}

    def hotunplugDisk(self, params):
        diskParams = params.get('drive', {})
        diskParams['path'] = self.cif.prepareVolumePath(diskParams)

        # Find disk object in vm's drives list
        drive = None
        for drv in self._devices[vm.DISK_DEVICES][:]:
            if drv.path == diskParams['path']:
                drive = drv
                break

        if drive:
            driveXml = drive.getXML().toprettyxml(encoding='utf-8')
            self.log.debug("Hotunplug disk xml: %s", driveXml)
        else:
            self.log.error("Hotunplug disk failed - Disk not found: %s", diskParams)
            return {'status' : {'code': errCode['hotunplugDisk']['status']['code'],
                                'message': "Disk not found"}}

        # Remove found disk from vm's drives list
        if drive:
            self._devices[vm.DISK_DEVICES].remove(drive)
        # Find and remove disk device from vm's conf
        diskDev = None
        for dev in self.conf['devices'][:]:
            if dev['type'] == vm.DISK_DEVICES and \
                                        dev['path'] == diskParams['path']:
                self.conf['devices'].remove(dev)
                diskDev = dev
                break

        self.saveState()

        try:
            self._dom.detachDevice(driveXml)
        except libvirt.libvirtError, e:
            self.log.error("Hotunplug failed", exc_info=True)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return errCode['noVM']
            # Restore disk device in vm's conf and _devices
            if diskDev:
                self.conf['devices'].append(diskDev)
            if drive:
                self._devices[vm.DISK_DEVICES].append(drive)
            self.saveState()
            return {'status' : {'code': errCode['hotunplugDisk']['status']['code'],
                                'message': e.message}}
        else:
            self._cleanup()

        return {'status': doneCode, 'vmList': self.cif.vmContainer[params['vmId']].status()}

    def _readPauseCode(self, timeout):
        self.log.warning('_readPauseCode unsupported by libvirt vm')
        return 'NOERR'

    def _monitorDependentInit(self, timeout=None):
        self.log.warning('unsupported by libvirt vm')

    def _timeoutExperienced(self, timeout):
        if timeout:
            self._monitorResponse = -1
        else:
            self._monitorResponse = 0

    def _waitForIncomingMigrationFinish(self):
        if 'restoreState' in self.conf:
            self.cont()
            del self.conf['restoreState']
            hooks.after_vm_dehibernate(self._dom.XMLDesc(0), self.conf)
        elif 'migrationDest' in self.conf:
            timeout = config.getint('vars', 'migration_timeout')
            self.log.debug("Waiting %s seconds for end of migration" % timeout)
            self._incomingMigrationFinished.wait(timeout)
            try:
                # Would fail if migration isn't successful,
                # or restart vdsm if connection to libvirt was lost
                self._dom = NotifyingVirDomain(
                                self._connection.lookupByUUIDString(self.id),
                                self._timeoutExperienced)
            except Exception, e:
                # Improve description of exception
                if not self._incomingMigrationFinished.isSet():
                    newMsg = '%s - Timed out (did not recieve success event)' % (e.args[0] if len(e.args) else 'Migration Error')
                    e.args = (newMsg,) + e.args[1:]
                raise

            self._domDependentInit()
            del self.conf['migrationDest']
            del self.conf['afterMigrationStatus']
            hooks.after_vm_migrate_destination(self._dom.XMLDesc(0), self.conf)
        if 'guestIPs' in self.conf:
            del self.conf['guestIPs']
        if 'username' in self.conf:
            del self.conf['username']
        self.saveState()
        self.log.debug("End of migration")

    def _underlyingCont(self):
        hooks.before_vm_cont(self._dom.XMLDesc(0), self.conf)
        self._dom.resume()

    def _underlyingPause(self):
        hooks.before_vm_pause(self._dom.XMLDesc(0), self.conf)
        self._dom.suspend()

    def snapshot(self, snapDrives):
        """Live snapshot command"""

        def _diskSnapshot(vmDev, newpath):
            """Libvirt snapshot XML"""

            disk = xml.dom.minidom.Element('disk')
            disk.setAttribute('name', vmDev)
            disk.setAttribute('snapshot', 'external')

            source = xml.dom.minidom.Element('source')
            source.setAttribute('file', newpath)

            disk.appendChild(source)
            return disk

        def _normSnapDriveParams(drive):
            """Normalize snapshot parameters"""

            if drive.has_key("baseVolumeID"):
                baseDrv = {"domainID": drive["domainID"],
                           "imageID": drive["imageID"],
                           "volumeID": drive["baseVolumeID"]}
                tgetDrv = baseDrv.copy()
                tgetDrv["volumeID"] = drive["volumeID"]

            elif drive.has_key("baseGUID"):
                baseDrv = {"GUID": drive["baseGUID"]}
                tgetDrv = {"GUID": drive["GUID"]}

            elif drive.has_key("baseUUID"):
                baseDrv = {"UUID": drive["baseUUID"]}
                tgetDrv = {"UUID": drive["UUID"]}

            else:
                baseDrv, tgetDrv = (None, None)

            return baseDrv, tgetDrv

        def _findSnapDrive(drive):
            """Find a drive given its definition"""

            if drive.has_key("domainID"):
                tgetDrv = (drive["domainID"], drive["imageID"],
                           drive["volumeID"])

                for device in self._devices[vm.DISK_DEVICES][:]:
                    if not hasattr(device, "domainID"):
                        continue
                    if ((device.domainID, device.imageID,
                            device.volumeID) == tgetDrv):
                        return device

            elif drive.has_key("GUID"):
                for device in self._devices[vm.DISK_DEVICES][:]:
                    if not hasattr(device, "GUID"):
                        continue
                    if device.GUID == drive["GUID"]:
                        return device

            elif drive.has_key("UUID"):
                for device in self._devices[vm.DISK_DEVICES][:]:
                    if not hasattr(device, "UUID"):
                        continue
                    if device.UUID == drive["UUID"]:
                        return device

            return None

        def _rollbackDrives(newDrives):
            """Rollback the prepared volumes for the snapshot"""

            for vmDevName, drive in newDrives.iteritems():
                try:
                    self.cif.teardownVolumePath(drive)
                except:
                    self.log.error("Unable to teardown drive: %s", vmDevName,
                                   exc_info=True)

        def _updateDrive(drive):
            """Update the drive with the new volume information"""

            # Updating the drive object
            for device in self._devices[vm.DISK_DEVICES][:]:
                if device.name == drive["name"]:
                    for k, v in drive.iteritems():
                        setattr(device, k, v)
                    break
            else:
                self.log.error("Unable to update the drive object for: %s",
                               drive["name"])

            # Updating the VM configuration
            for device in self.conf["devices"][:]:
                if (device['type'] == vm.DISK_DEVICES and
                        device.get("name") == drive["name"]):
                    device.update(drive)
                    break
            else:
                self.log.error("Unable to update the device configuration ",
                               "for: %s", drive["name"])

            self.saveState()

        snap = xml.dom.minidom.Element('domainsnapshot')
        disks = xml.dom.minidom.Element('disks')
        newDrives = {}

        for drive in snapDrives:
            baseDrv, tgetDrv = _normSnapDriveParams(drive)

            if _findSnapDrive(tgetDrv):
                # The snapshot volume is the current one, skipping
                self.log.debug("The volume is already in use: %s", tgetDrv)
                continue

            vmDrive = _findSnapDrive(baseDrv)

            if vmDrive is None:
                # The volume we want to snapshot doesn't exist
                _rollbackDrives(newDrives)
                self.log.error("The base volume doesn't exist: %s", baseDrv)
                return errCode['snapshotErr']

            vmDevName = vmDrive.name

            newDrives[vmDevName] = tgetDrv.copy()
            newDrives[vmDevName]["poolID"] = vmDrive.poolID
            newDrives[vmDevName]["name"] = vmDevName

            try:
                newDrives[vmDevName]["path"] = \
                            self.cif.prepareVolumePath(newDrives[vmDevName])
            except Exception:
                _rollbackDrives(newDrives)
                self.log.error("Unable to prepare the volume path "
                               "for the disk: %s", vmDevName, exc_info=True)
                return errCode['snapshotErr']

            snapelem = _diskSnapshot(vmDevName, newDrives[vmDevName]["path"])
            disks.appendChild(snapelem)

        # If all the drives are the corrent ones, return success
        if len(newDrives) == 0:
            self.log.debug("All the drives are already in use, success")
            return {'status': doneCode}

        snap.appendChild(disks)
        snapxml = snap.toprettyxml()

        self.log.debug(snapxml)
        self._volumesPrepared = False

        try:
            self._dom.snapshotCreateXML(snapxml,
                libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY)
        except:
            self.log.error("Unable to take snapshot", exc_info=True)
            return errCode['snapshotErr']
        else:
            # Update the drive information
            for drive in newDrives.values(): _updateDrive(drive)
        finally:
            self._volumesPrepared = True

        return {'status': doneCode}

    def changeCD(self, drivespec):
        return self._changeBlockDev('cdrom', 'hdc', drivespec)

    def changeFloppy(self, drivespec):
        return self._changeBlockDev('floppy', 'fda', drivespec)

    def _changeBlockDev(self, vmDev, blockdev, drivespec):
        try:
            path = self.cif.prepareVolumePath(drivespec)
        except vm.VolumeError, e:
            return {'status': {'code': errCode['imageErr']['status']['code'],
              'message': errCode['imageErr']['status']['message'] % str(e)}}
        diskelem = xml.dom.minidom.Element('disk')
        diskelem.setAttribute('type', 'file')
        diskelem.setAttribute('device', vmDev)
        source = xml.dom.minidom.Element('source')
        source.setAttribute('file', path)
        diskelem.appendChild(source)
        target = xml.dom.minidom.Element('target')
        target.setAttribute('dev', blockdev)
        diskelem.appendChild(target)

        try:
            self._dom.updateDeviceFlags(diskelem.toxml(),
                                  libvirt.VIR_DOMAIN_DEVICE_MODIFY_FORCE)
        except:
            self.log.debug(traceback.format_exc())
            self.cif.teardownVolumePath(drivespec)
            return {'status': {'code': errCode['changeDisk']['status']['code'],
              'message': errCode['changeDisk']['status']['message']}}
        self.cif.teardownVolumePath(self.conf.get(vmDev))
        self.conf[vmDev] = path
        return {'status': doneCode, 'vmList': self.status()}

    def setTicket(self, otp, seconds, connAct):
        graphics = xml.dom.minidom.parseString(self._dom.XMLDesc(0)) \
                          .childNodes[0].getElementsByTagName('graphics')[0]
        graphics.setAttribute('passwd', otp)
        if int(seconds) > 0:
            validto = time.strftime('%Y-%m-%dT%H:%M:%S',
                                    time.gmtime(time.time() + float(seconds)))
            graphics.setAttribute('passwdValidTo', validto)
        if graphics.getAttribute('type') == 'spice':
            graphics.setAttribute('connected', connAct)
        self._dom.updateDeviceFlags(graphics.toxml(), 0)
        return {'status': doneCode}

    def _reviveTicket(self, newlife):
        """Revive an existing ticket, if it has expired or about to expire"""
        graphics = xml.dom.minidom.parseString(
                      self._dom.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE)) \
                      .childNodes[0].getElementsByTagName('graphics')[0]
        validto = max(time.strptime(graphics.getAttribute('passwdValidTo'),
                                   '%Y-%m-%dT%H:%M:%S'),
                      time.gmtime(time.time() + newlife))
        graphics.setAttribute('passwdValidTo',
                time.strftime('%Y-%m-%dT%H:%M:%S', validto))
        graphics.setAttribute('connected', 'keep')
        self._dom.updateDeviceFlags(graphics.toxml(), 0)


    def _onAbnormalStop(self, blockDevAlias, err):
        """
        Called back by IO_ERROR_REASON event

        :param err: one of "eperm", "eio", "enospc" or "eother"
        Note the different API from that of Vm._onAbnormalStop
        """
        self.log.info('abnormal vm stop device %s error %s', blockDevAlias, err)
        self.conf['pauseCode'] = err.upper()
        self._guestCpuRunning = False
        if err.upper() == 'ENOSPC':
            for d in self._devices[vm.DISK_DEVICES]:
                if d.alias == blockDevAlias:
                    #in the case of a qcow2-like file stored inside a block
                    #device 'physical' will give the block device size, while
                    #'allocation' will give the qcow2 image size
                    #D. Berrange
                    capacity, alloc, physical = self._dom.blockInfo(d.path, 0)
                    if  physical > (alloc + config.getint('irs',
                                    'volume_utilization_chunk_mb')):
                        self.log.warn('%s = %s/%s error %s phys: %s alloc: %s \
                                      Ingnoring already managed event.',
                                      blockDevAlias, d.domainID, d.volumeID,
                                      err, physical, alloc)
                        return
                    self.log.info('%s = %s/%s error %s phys: %s alloc: %s',
                                  blockDevAlias, d.domainID, d.volumeID, err,
                                  physical, alloc)
                    self._lvExtend(d.name)

    def _acpiShutdown(self):
        self._dom.shutdown()

    def _getPid(self):
        pid = '0'
        try:
            rc, out, err = utils.execCmd([constants.EXT_GET_VM_PID,
                                          self.conf['vmName'].encode('utf-8')],
                                         raw=True)
            if rc == 0:
                pid = out
        except:
            pass
        return pid

    def _getUnderlyingVmInfo(self):
        self._lastXMLDesc = self._dom.XMLDesc(0)
        devxml = xml.dom.minidom.parseString(self._lastXMLDesc) \
                    .childNodes[0].getElementsByTagName('devices')[0]
        self._devXmlHash = str(hash(devxml.toxml()))

        return self._lastXMLDesc

    def saveState(self):
        vm.Vm.saveState(self)
        try:
            self._getUnderlyingVmInfo()
        except:
            # we do not care if _dom suddenly died now
            pass

    def _ejectFloppy(self):
        if 'volatileFloppy' in self.conf:
            utils.rmFile(self.conf['floppy'])
        self._changeBlockDev('floppy', 'fda', '')

    def releaseVm(self):
        """
        Stop VM and release all resources
        """
        with self._releaseLock:
            if self._released:
                return {'status': doneCode}

            self.log.info('Release VM resources')
            self.lastStatus = 'Powering down'
            try:
                if self._vmStats:
                    self._vmStats.stop()
                if self.guestAgent:
                    self.guestAgent.stop()
                if self._dom:
                    self._dom.destroy()
            except libvirt.libvirtError, e:
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    self.log.warning(traceback.format_exc())
                else:
                    self.log.warn("VM %s is not running", self.conf['vmId'])

            self.cif.ksmMonitor.adjust()
            self._cleanup()

            self.cif.irs.inappropriateDevices(self.id)

            hooks.after_vm_destroy(self._lastXMLDesc, self.conf)

            self._released = True

        return {'status': doneCode}

    def deleteVm(self):
        """
        Clean VM from the system
        """
        try:
            del self.cif.vmContainer[self.conf['vmId']]
            self.log.debug("Total desktops after destroy of %s is %d",
                     self.conf['vmId'], len(self.cif.vmContainer))
        except Exception:
            self.log.error("Failed to delete VM %s", self.conf['vmId'], exc_info=True)

    def destroy(self):
        self.log.debug('destroy Called')
        hooks.before_vm_destroy(self._lastXMLDesc, self.conf)
        self.destroyed = True

        response = self.releaseVm()
        if response['status']['code']:
            return response
        # Clean VM from the system
        self.deleteVm()

        return {'status': doneCode}

    def getStats(self):
        stats = vm.Vm.getStats(self)
        stats['hash'] = self._devXmlHash
        return stats

    def _getUnderlyingDeviceAddress(self, devXml):
        """
        Obtain device's address from libvirt
        """
        address = {}
        adrXml = devXml.getElementsByTagName('address')[0]
        # Parse address to create proper dictionary.
        # Libvirt device's address definition is:
        # PCI = {'type':'pci', 'domain':'0x0000', 'bus':'0x00',
        #        'slot':'0x0c', 'function':'0x0'}
        # IDE = {'type':'drive', 'controller':'0', 'bus':'0', 'unit':'0'}
        for key in adrXml.attributes.keys():
            address[key] = adrXml.getAttribute(key)

        return address

    def _getUnderlyingUnknownDeviceInfo(self):
        """
        Obtain unknown devices info from libvirt.

        Unknown device is a device that has an address but wasn't
        passed during VM creation request.
        """
        def isKnownDevice(alias):
            for dev in self.conf['devices']:
                if dev['alias'] == alias:
                    return True
            return False

        devsxml = xml.dom.minidom.parseString(self._lastXMLDesc) \
                    .childNodes[0].getElementsByTagName('devices')[0]

        for x in devsxml.childNodes:
            # Ignore empty nodes and devices without address
            if x.nodeName == '#text' or \
                not x.getElementsByTagName('address'):
                continue

            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            if not isKnownDevice(alias):
                address = self._getUnderlyingDeviceAddress(x)
                # I general case we assume that device has attribute 'type',
                # if it hasn't getAttribute returns ''.
                device = x.getAttribute('type')
                newDev = {'type': x.nodeName,
                          'alias': alias,
                          'device': device,
                          'address': address}
                self.conf['devices'].append(newDev)

    def _getUnderlyingControllerDeviceInfo(self):
        """
        Obtain controller devices info from libvirt.
        """
        ctrlsxml = xml.dom.minidom.parseString(self._lastXMLDesc) \
                    .childNodes[0].getElementsByTagName('devices')[0] \
                    .getElementsByTagName('controller')
        for x in ctrlsxml:
            # Ignore controller devices without address
            if not x.getElementsByTagName('address'):
                continue
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            device = x.getAttribute('type')
            # Get controller address
            address = self._getUnderlyingDeviceAddress(x)

            for ctrl in self._devices[vm.CONTROLLER_DEVICES]:
                if ctrl.device == device:
                    ctrl.alias = alias
                    ctrl.address = address
            # Update vm's conf with address for known controller devices
            knownDev = False
            for dev in self.conf['devices']:
                if (dev['type'] == vm.CONTROLLER_DEVICES) and \
                                            (dev['device'] == device):
                    dev['address'] = address
                    dev['alias'] = alias
                    knownDev = True
            # Add unknown controller device to vm's conf
            if not knownDev:
                self.conf['devices'].append({'type': vm.CONTROLLER_DEVICES,
                                             'device': device,
                                             'address': address,
                                             'alias': alias})

    def _getUnderlyingVideoDeviceInfo(self):
        """
        Obtain video devices info from libvirt.
        """
        videosxml = xml.dom.minidom.parseString(self._lastXMLDesc) \
                    .childNodes[0].getElementsByTagName('devices')[0] \
                    .getElementsByTagName('video')
        for x in videosxml:
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            # Get video card address
            address = self._getUnderlyingDeviceAddress(x)

            # FIXME. We have an identification problem here.
            # Video card device has not unique identifier, except the alias
            # (but backend not aware to device's aliases).
            # So, for now we can only assign the address according to devices order.
            for vc in self._devices[vm.VIDEO_DEVICES]:
                if not hasattr(vc, 'address'):
                    vc.alias = alias
                    vc.address = address
            # Update vm's conf with address
            for dev in self.conf['devices']:
                if (dev['type'] == vm.VIDEO_DEVICES) and not dev.get('address'):
                    dev['address'] = address
                    dev['alias'] = alias

    def _getUnderlyingSoundDeviceInfo(self):
        """
        Obtain sound devices info from libvirt.
        """
        soundsxml = xml.dom.minidom.parseString(self._lastXMLDesc) \
                    .childNodes[0].getElementsByTagName('devices')[0] \
                    .getElementsByTagName('sound')
        for x in soundsxml:
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            # Get sound card address
            address = self._getUnderlyingDeviceAddress(x)

            # FIXME. We have an identification problem here.
            # Sound device has not unique identifier, except the alias
            # (but backend not aware to device's aliases).
            # So, for now we can only assign the address according to devices order.
            for sc in self._devices[vm.SOUND_DEVICES]:
                if not hasattr(sc, 'address'):
                    sc.alias = alias
                    sc.address = address
            # Update vm's conf with address
            for dev in self.conf['devices']:
                if (dev['type'] == vm.SOUND_DEVICES) and not dev.get('address'):
                    dev['address'] = address
                    dev['alias'] = alias

    def _getUnderlyingDriveInfo(self):
        """
        Obtain block devices info from libvirt.
        """
        disksxml = xml.dom.minidom.parseString(self._lastXMLDesc) \
                    .childNodes[0].getElementsByTagName('devices')[0] \
                    .getElementsByTagName('disk')
        # FIXME!  We need to gather as much info as possible from the libvirt.
        # In the future we can return this real data to managment instead of
        # vm's conf
        for x in disksxml:
            sources = x.getElementsByTagName('source')
            if sources:
                devPath = sources[0].getAttribute('file') \
                            or sources[0].getAttribute('dev')
            else:
                devPath = ''

            target = x.getElementsByTagName('target')
            if target:
                name = target[0].getAttribute('dev')
            else:
                name = ''

            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            readonly = bool(x.getElementsByTagName('readonly'))
            boot = x.getElementsByTagName('boot')
            bootOrder = boot[0].getAttribute('order') if boot else ''

            devType = x.getAttribute('device')
            if devType == 'disk':
                drv = x.getElementsByTagName('driver')[0].getAttribute('type') # raw/qcow2
            else:
                drv = 'raw'
            # Get disk address
            address = self._getUnderlyingDeviceAddress(x)

            for d in self._devices[vm.DISK_DEVICES]:
                if d.path == devPath:
                    d.name = name
                    d.type = devType
                    d.drv = drv
                    d.alias = alias
                    d.address = address
                    d.readonly = readonly
                    if bootOrder:
                        d.bootOrder = bootOrder
            # Update vm's conf with address for known disk devices
            knownDev = False
            for dev in self.conf['devices']:
                if dev['type'] == vm.DISK_DEVICES and dev['path'] == devPath:
                    dev['name'] = name
                    dev['address'] = address
                    dev['alias'] = alias
                    dev['readonly'] = str(readonly)
                    if bootOrder:
                        dev['bootOrder'] = bootOrder
                    knownDev = True
            # Add unknown disk device to vm's conf
            if not knownDev:
                iface = 'ide' if address['type'] == 'drive' else 'pci'
                diskDev = {'type': vm.DISK_DEVICES, 'device': devType,
                           'iface': iface, 'path': devPath, 'name': name,
                           'address': address, 'alias': alias}
                diskDev['readonly'] = str(readonly)
                if bootOrder:
                    diskDev['bootOrder'] = bootOrder
                self.conf['devices'].append(diskDev)

    def _getUnderlyingDisplayPort(self):
        """
        Obtain display port info from libvirt.
        """
        graphics = xml.dom.minidom.parseString(self._lastXMLDesc) \
                          .childNodes[0].getElementsByTagName('graphics')[0]
        port = graphics.getAttribute('port')
        if port:
            self.conf['displayPort'] = port
        port = graphics.getAttribute('tlsPort')
        if port:
            self.conf['displaySecurePort'] = port

    def _getUnderlyingNetworkInterfaceInfo(self):
        """
        Obtain network interface info from libvirt.
        """
        # TODO use xpath instead of parseString (here and elsewhere)
        ifsxml = xml.dom.minidom.parseString(self._lastXMLDesc) \
                    .childNodes[0].getElementsByTagName('devices')[0] \
                    .getElementsByTagName('interface')
        for x in ifsxml:
            devType = x.getAttribute('type')
            name = x.getElementsByTagName('target')[0].getAttribute('dev')
            mac = x.getElementsByTagName('mac')[0].getAttribute('address')
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            model = x.getElementsByTagName('model')[0].getAttribute('type')
            bridge = x.getElementsByTagName('source')[0].getAttribute('bridge')
            # Get nic address
            address = self._getUnderlyingDeviceAddress(x)
            for nic in self._devices[vm.NIC_DEVICES]:
                if nic.macAddr.lower() == mac.lower():
                    nic.name = name
                    nic.alias = alias
                    nic.address = address
            # Update vm's conf with address for known nic devices
            knownDev = False
            for dev in self.conf['devices']:
                if dev['type'] == vm.NIC_DEVICES and dev['macAddr'] == mac:
                    dev['address'] = address
                    dev['alias'] = alias
                    knownDev = True
            # Add unknown nic device to vm's conf
            if not knownDev:
                self.conf['devices'].append({'type': vm.NIC_DEVICES,
                                             'device': devType,
                                             'macAddr': mac,
                                             'nicModel': model,
                                             'network': bridge,
                                             'address': address,
                                             'alias': alias})

    def _setWriteWatermarks(self):
        """
        Define when to receive an event about high write to guest image
        Currently unavailable by libvirt.
        """
        pass

    def _onLibvirtLifecycleEvent(self, event, detail, opaque):
        self.log.debug('event %s detail %s opaque %s',
                       libvirtev.eventToString(event), detail, opaque)
        if event == libvirt.VIR_DOMAIN_EVENT_STOPPED:
            if detail == libvirt.VIR_DOMAIN_EVENT_STOPPED_MIGRATED and \
                self.lastStatus == 'Migration Source':
                hooks.after_vm_migrate_source(self._lastXMLDesc, self.conf)
            elif detail == libvirt.VIR_DOMAIN_EVENT_STOPPED_SAVED and \
                self.lastStatus == 'Saving State':
                hooks.after_vm_hibernate(self._lastXMLDesc, self.conf)
            else:
                if detail == libvirt.VIR_DOMAIN_EVENT_STOPPED_SHUTDOWN:
                    self.user_destroy = True
                self._onQemuDeath()
        elif event == libvirt.VIR_DOMAIN_EVENT_SUSPENDED:
            self._guestCpuRunning = False
            if detail == libvirt.VIR_DOMAIN_EVENT_SUSPENDED_PAUSED:
                hooks.after_vm_pause(self._dom.XMLDesc(0), self.conf)
        elif event == libvirt.VIR_DOMAIN_EVENT_RESUMED:
            self._guestCpuRunning = True
            if detail == libvirt.VIR_DOMAIN_EVENT_RESUMED_UNPAUSED:
                hooks.after_vm_cont(self._dom.XMLDesc(0), self.conf)
            elif detail == libvirt.VIR_DOMAIN_EVENT_RESUMED_MIGRATED and\
                self.lastStatus == 'Migration Destination':
                self._incomingMigrationFinished.set()

    def waitForMigrationDestinationPrepare(self):
        """Wait until paths are prepared for migration destination"""
        prepareTimeout = self._loadCorrectedTimeout(
                          config.getint('vars', 'migration_listener_timeout'),
                          doubler=5)
        self.log.debug('migration destination: waiting %ss for path preparation', prepareTimeout)
        self._pathsPreparedEvent.wait(prepareTimeout)
        if not self._pathsPreparedEvent.isSet():
            self.log.debug('Timeout while waiting for path preparation')
            return False
        srcDomXML = self.conf.pop('_srcDomXML')
        hooks.before_vm_migrate_destination(srcDomXML, self.conf)
        return True

# A little unrelated hack to make xml.dom.minidom.Document.toprettyxml()
# not wrap Text node with whitespace.
# until http://bugs.python.org/issue4147 is accepted
def __hacked_writexml(self, writer, indent="", addindent="", newl=""):
    # copied from xml.dom.minidom.Element.writexml and hacked not to wrap Text
    # nodes with whitespace.

    # indent = current indentation
    # addindent = indentation to add to higher levels
    # newl = newline string
    writer.write(indent+"<" + self.tagName)

    attrs = self._get_attributes()
    a_names = attrs.keys()
    a_names.sort()

    for a_name in a_names:
        writer.write(" %s=\"" % a_name)
        #_write_data(writer, attrs[a_name].value) # replaced
        xml.dom.minidom._write_data(writer, attrs[a_name].value)
        writer.write("\"")
    if self.childNodes:
        # added special handling of Text nodes
        if len(self.childNodes) == 1 and \
           isinstance(self.childNodes[0], xml.dom.minidom.Text):
            writer.write(">")
            self.childNodes[0].writexml(writer)
            writer.write("</%s>%s" % (self.tagName,newl))
        else:
            writer.write(">%s"%(newl))
            for node in self.childNodes:
                node.writexml(writer,indent+addindent,addindent,newl)
            writer.write("%s</%s>%s" % (indent,self.tagName,newl))
    else:
        writer.write("/>%s"%(newl))
xml.dom.minidom.Element.writexml = __hacked_writexml

