#
# Copyright 2008-2015 Red Hat, Inc.
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

from vdsm.utils import monotonic_time
from .utils import isVdsmImage


_MBPS_TO_BPS = 10 ** 6 / 8


def produce(vm, first_sample, last_sample, interval):
    """
    Translates vm samples into stats.
    """

    stats = {}

    cpu(stats, first_sample, last_sample, interval)
    networks(vm, stats, first_sample, last_sample, interval)
    disks(vm, stats, first_sample, last_sample, interval)
    balloon(vm, stats, last_sample)
    cpu_count(stats, last_sample)
    tune_io(vm, stats)

    return stats


def tune_io(vm, stats):
    """
    Collect the current ioTune settings for all disks VDSM knows about.

    This assumes VDSM always has the correct info and nobody else is
    touching the device without telling VDSM about it.

    TODO: We might want to move to XML parsing (first update) and events
    once libvirt supports them:
    https://bugzilla.redhat.com/show_bug.cgi?id=1114492
    """
    io_tune_info = []

    for disk in vm.getDiskDevices():
        if "ioTune" in disk.specParams:
            io_tune_info.append({
                "name": disk.name,
                "path": disk.path,
                "ioTune": disk.specParams["ioTune"]
            })

    stats['ioTune'] = io_tune_info


def cpu(stats, first_sample, last_sample, interval):
    stats['cpuUser'] = 0.0
    stats['cpuSys'] = 0.0

    if first_sample is None or last_sample is None:
        return
    if interval <= 0:
        logging.warning(
            'invalid interval %i when computing CPU stats',
            interval)
        return

    try:
        stats['cpuUsage'] = str(last_sample['cpu.system'] +
                                last_sample['cpu.user'])

        stats['cpuSys'] = _usage_percentage(
            _diff(last_sample, first_sample, 'cpu.user') +
            _diff(last_sample, first_sample, 'cpu.system'),
            interval)
        stats['cpuUser'] = _usage_percentage(
            _diff(last_sample, first_sample, 'cpu.time')
            - _diff(last_sample, first_sample, 'cpu.user')
            - _diff(last_sample, first_sample, 'cpu.system'),
            interval)

    except KeyError as e:
        logging.exception("CPU stats not available: %s", e)


def balloon(vm, stats, sample):
    max_mem = int(vm.conf.get('memSize')) * 1024

    for dev in vm.getBalloonDevicesConf():
        if dev['specParams']['model'] != 'none':
            balloon_target = dev.get('target', max_mem)
            break
    else:
        balloon_target = None

    stats['balloonInfo'] = {}

    # Do not return any balloon status info before we get all data
    # MOM will ignore VMs with missing balloon information instead
    # using incomplete data and computing wrong balloon targets
    if balloon_target is not None and sample is not None:
        stats['balloonInfo'].update({
            'balloon_max': str(max_mem),
            'balloon_min': str(
                int(vm.conf.get('memGuaranteedSize', '0')) * 1024),
            'balloon_cur': str(sample['balloon.current']),
            'balloon_target': str(balloon_target)
        })


def cpu_count(stats, sample):
    # Handling the case when not enough samples exist
    if sample is None:
        return

    if 'vcpu.current' in sample:
        vcpu_count = sample['vcpu.current']
        if vcpu_count != -1:
            stats['vcpuCount'] = vcpu_count
        else:
            logging.error('Failed to get VM cpu count')


def nic(name, model, mac, start_sample, end_sample, interval):
    ifSpeed = [100, 1000][model in ('e1000', 'virtio')]

    ifStats = {'macAddr': mac,
               'name': name,
               'speed': str(ifSpeed),
               'state': 'unknown'}

    ifStats['rxErrors'] = str(end_sample['rx.errs'])
    ifStats['rxDropped'] = str(end_sample['rx.drop'])
    ifStats['txErrors'] = str(end_sample['tx.errs'])
    ifStats['txDropped'] = str(end_sample['tx.drop'])

    rxDelta = (
        end_sample['rx.bytes'] - start_sample['rx.bytes'])
    ifRxBytes = (100.0 *
                 (rxDelta % 2 ** 32) /
                 interval / ifSpeed / _MBPS_TO_BPS)
    txDelta = (
        end_sample['tx.bytes'] - start_sample['tx.bytes'])
    ifTxBytes = (100.0 *
                 (txDelta % 2 ** 32) /
                 interval / ifSpeed / _MBPS_TO_BPS)

    ifStats['rxRate'] = '%.1f' % ifRxBytes
    ifStats['txRate'] = '%.1f' % ifTxBytes

    ifStats['rx'] = str(end_sample['rx.bytes'])
    ifStats['tx'] = str(end_sample['tx.bytes'])
    ifStats['sampleTime'] = monotonic_time()

    return ifStats


def networks(vm, stats, first_sample, last_sample, interval):
    stats['network'] = {}

    if first_sample is None or last_sample is None:
        return

    for nic in vm.getNicDevices():
        if nic.name.startswith('hostdev'):
            continue

        first_nic = first_sample.get('net', {}).get(nic.name, {})
        last_nic = last_sample.get('net', {}).get(nic.name, {})
        # may happen if nic is a new hot-plugged one
        if not first_nic or not last_nic:
            continue
        stats['network'][nic.name] = nic(
            nic.name, nic.nicModel, nic.macAddr,
            first_nic, last_nic, interval)


def disks(vm, stats, first_sample, last_sample, interval):
    if first_sample is None or last_sample is None:
        return

    for vm_drive in vm.getDiskDevices():
        drive_stats = {}
        try:
            drive_stats = {
                'truesize': str(vm_drive.truesize),
                'apparentsize': str(vm_drive.apparentsize),
                'readLatency': '0',
                'writeLatency': '0',
                'flushLatency': '0'
            }
            if isVdsmImage(vm_drive):
                drive_stats['imageID'] = vm_drive.imageID
            elif "GUID" in vm_drive:
                drive_stats['lunGUID'] = vm_drive.GUID
            first_disk = first_sample.get('block', {}).get(vm_drive.name, {})
            last_disk = last_sample.get('block', {}).get(vm_drive.name, {})
            if first_disk and last_disk:
                # will be None if sampled during recovery
                if interval > 0:
                    drive_stats.update(
                        _disk_rate(first_disk, last_disk, interval))
                    drive_stats.update(
                        _disk_latency(first_disk, last_disk))
                else:
                    logging.warning(
                        'invalid interval %i when calculating '
                        'stats for vm %s disk %s',
                        interval, vm.id, vm_drive.name)

                drive_info = last_sample[vm_drive.name]
                drive_stats['readOps'] = str(drive_info['rd.reqs'])
                drive_stats['writeOps'] = str(drive_info['wr.reqs'])
                drive_stats['readBytes'] = str(drive_info['rd.bytes'])
                drive_stats['writtenBytes'] = str(drive_info['wr.bytes'])

        except AttributeError:
            logging.exception("Disk %s stats not available",
                              vm_drive.name)

        stats[vm_drive.name] = drive_stats


def _disk_rate(first_sample, last_sample, interval):
    return {
        'readRate': (
            (last_sample['rd.bytes'] - first_sample['rd.bytes'])
            / interval),
        'writeRate': (
            (last_sample['wr.bytes'] - first_sample['wr.bytes'])
            / interval)}


def _disk_latency(first_sample, last_sample):
    def compute_latency(ltype):
        ops = ltype + '.reqs'
        operations = last_sample[ops] - first_sample[ops]
        if not operations:
            return 0
        times = ltype + '.times'
        elapsed_time = last_sample[times] - first_sample[times]
        return elapsed_time / operations

    return {'readLatency': str(compute_latency('rd')),
            'writeLatency': str(compute_latency('wr')),
            'flushLatency': str(compute_latency('fl'))}


def _diff(prev, curr, val):
    return prev[val] - curr[val]


def _usage_percentage(val, interval):
    return 100 * val / interval / 1000 ** 3
