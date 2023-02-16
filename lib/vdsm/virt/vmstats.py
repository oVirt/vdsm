# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import contextlib
import logging

import six

from vdsm.common.time import monotonic_time
from vdsm.utils import convertToStr

from vdsm.virt.utils import isVdsmImage


_log = logging.getLogger('virt.vmstats')


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
    memory(stats, first_sample, last_sample, interval)

    return stats


def translate(vm_stats):
    stats = {}

    for var in vm_stats:
        if var == "ioTune":
            value = vm_stats[var]
            if value:
                # Convert ioTune numbers to strings to avoid xml-rpc issue
                # with numbers bigger than int32_t
                for ioTune in value:
                    ioTune["ioTune"] = dict(
                        (k, convertToStr(v)) for k, v
                        in six.iteritems(ioTune["ioTune"]))
                stats[var] = vm_stats[var]
        elif type(vm_stats[var]) is not dict:
            stats[var] = convertToStr(vm_stats[var])
        elif var in ('disks', 'network', 'balloonInfo', 'memoryStats'):
            value = vm_stats[var]
            if value:
                stats[var] = value

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
        iotune = disk.iotune
        if iotune:
            io_tune_info.append({
                "name": disk.name,
                "path": disk.path,
                "ioTune": iotune
            })

    stats['ioTune'] = io_tune_info


def cpu(stats, first_sample, last_sample, interval):
    """
    Add cpu statistics to the `stats' dict:
    - cpuUser
    - cpuSys
    - cpuTime
    - cpuActual
    Expect two samplings `first_sample' and `last_sample'
    which must be data in the format of the libvirt bulk stats.
    `interval' is the time between the two samplings, in seconds.
    Fill `stats' as much as possible, bailing out at first error.
    Return None on error,  if any needed data is missing or wrong.
    Return the `stats' dictionary on success.
    """
    stats['cpuUser'] = 0.0
    stats['cpuSys'] = 0.0
    stats['cpuUsage'] = 0.0
    stats['cpuActual'] = False

    if first_sample is None or last_sample is None:
        return None
    if interval <= 0:
        _log.warning(
            'invalid interval %i when computing CPU stats',
            interval)
        return None

    keys = ('cpu.system', 'cpu.user')
    samples = (last_sample, first_sample)

    if all(k in s for k in keys for s in samples):
        # TODO: cpuUsage should have the same type as cpuUser and cpuSys.
        # we may block the str() when xmlrpc is deserted.
        stats['cpuUsage'] = str(last_sample['cpu.system'] +
                                last_sample['cpu.user'])

        cpu_sys = ((last_sample['cpu.user'] - first_sample['cpu.user']) +
                   (last_sample['cpu.system'] - first_sample['cpu.system']))
        stats['cpuSys'] = _usage_percentage(cpu_sys, interval)

        if all('cpu.time' in s for s in samples):
            stats['cpuUser'] = _usage_percentage(
                ((last_sample['cpu.time'] - first_sample['cpu.time']) -
                 cpu_sys),
                interval)
            # To avoid negative values of stats['cpuUser']. It was coming
            # negative due to less accuracy of user_time and system_time
            # values (upto 2 decimal) as compared to cpu_time values
            # (upto 9 decimal) returned by libvirt
            if stats['cpuUser'] < 0:
                stats['cpuUser'] = 0.0

            stats['cpuActual'] = True
            return stats

    return None


def balloon(vm, stats, sample):
    max_mem = vm.mem_size_mb() * 1024
    balloon_info = vm.get_balloon_info()

    stats['balloonInfo'] = {}

    # Do not return any balloon status info before we get all data
    # MOM will ignore VMs with missing balloon information instead
    # using incomplete data and computing wrong balloon targets
    if (balloon_info and balloon_info['target'] is not None and
            sample is not None):

        balloon_cur = 0
        with _skip_if_missing_stats(vm):
            balloon_cur = sample['balloon.current']

        stats['balloonInfo'].update({
            'balloon_max': str(max_mem),
            'balloon_min': str(balloon_info['minimum']),
            'balloon_cur': str(balloon_cur),
            'balloon_target': str(balloon_info['target']),
            'ballooning_enabled': balloon_info['enabled'],
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
            _log.error('Failed to get VM cpu count')


def _nic_traffic(vm_obj, nic,
                 start_sample, start_index,
                 end_sample, end_index):
    """
    Return per-nic statistics packed into a dictionary
    - macAddr
    - name
    - speed
    - state
    - {rx,tx}Errors
    - {rx,tx}Dropped
    - {rx,tx}Rate
    - {rx,tx}
    - sampleTime
    Produce as many statistics as possible, skipping errors.
    Expect two samplings `start_sample' and `end_sample'
    which must be data in the format of the libvirt bulk stats.
    Expects the indexes of the nic whose statistics needs to be produced,
    for each sampling:
    `start_index' for `start_sample', `end_index' for `end_sample'.
    `vm_obj' is the Vm instance to which the nic belongs.
    `name', `model' and `mac' are the attributes of the said nic.
    Those three value are reported in the output stats.
    Return None on error,  if any needed data is missing or wrong.
    Return the `stats' dictionary on success.
    """

    if_stats = nic_info(nic)

    with _skip_if_missing_stats(vm_obj):
        if_stats['rxErrors'] = str(end_sample['net.%d.rx.errs' % end_index])
        if_stats['rxDropped'] = str(end_sample['net.%d.rx.drop' % end_index])
        if_stats['txErrors'] = str(end_sample['net.%d.tx.errs' % end_index])
        if_stats['txDropped'] = str(end_sample['net.%d.tx.drop' % end_index])

    with _skip_if_missing_stats(vm_obj):
        if_stats['rx'] = str(end_sample['net.%d.rx.bytes' % end_index])
        if_stats['tx'] = str(end_sample['net.%d.tx.bytes' % end_index])

    if_stats['sampleTime'] = monotonic_time()

    return if_stats


def networks(vm, stats, first_sample, last_sample, interval):
    stats['network'] = {}

    if first_sample is None or last_sample is None:
        return None
    if interval <= 0:
        _log.warning(
            'invalid interval %i when computing network stats for vm %s',
            interval, vm.id)
        return None

    first_indexes = _find_bulk_stats_reverse_map(first_sample, 'net')
    last_indexes = _find_bulk_stats_reverse_map(last_sample, 'net')

    for nic in vm.getNicDevices():
        if nic.is_hostdevice:
            continue

        # If Engine doesn't send `name' it's missing until we read the
        # updated XML from libvirt.
        if not hasattr(nic, 'name'):
            continue

        # may happen if nic is a new hot-plugged one
        if nic.name not in first_indexes or nic.name not in last_indexes:
            continue

        stats['network'][nic.name] = _nic_traffic(
            vm, nic,
            first_sample, first_indexes[nic.name],
            last_sample, last_indexes[nic.name])

    return stats


def nic_info(nic):
    info = {
        'macAddr': nic.macAddr,
        'name': nic.name,
        'speed': str(
            1000 if nic.nicModel in ('e1000', 'e1000e', 'virtio') else 100
        ),
        'state': 'unknown',
    }
    return info


def disks(vm, stats, first_sample, last_sample, interval):
    if first_sample is None or last_sample is None:
        return None

    # libvirt does not guarantee that disk will returned in the same
    # order across calls. It is usually like this, but not always,
    # for example if hotplug/hotunplug comes into play.
    # To be safe, we need to find the mapping after each call.
    first_indexes = _find_bulk_stats_reverse_map(first_sample, 'block')
    last_indexes = _find_bulk_stats_reverse_map(last_sample, 'block')
    disk_stats = {}

    for vm_drive in vm.getDiskDevices():
        drive_stats = {}
        try:
            drive_stats = disk_info(vm_drive)

            if (vm_drive.name in first_indexes and
               vm_drive.name in last_indexes):
                # will be None if sampled during recovery
                if interval <= 0:
                    _log.warning(
                        'invalid interval %i when calculating '
                        'stats for vm %s disk %s',
                        interval, vm.id, vm_drive.name)
                else:
                    drive_stats.update(
                        _disk_rate(
                            first_sample, first_indexes[vm_drive.name],
                            last_sample, last_indexes[vm_drive.name],
                            interval))
                drive_stats.update(
                    _disk_latency(
                        first_sample, first_indexes[vm_drive.name],
                        last_sample, last_indexes[vm_drive.name]))
                drive_stats.update(
                    _disk_iops_bytes(
                        first_sample, first_indexes[vm_drive.name],
                        last_sample, last_indexes[vm_drive.name]))

        except AttributeError:
            _log.exception("Disk %s stats not available",
                           vm_drive.name)

        disk_stats[vm_drive.name] = drive_stats

    if disk_stats:
        stats['disks'] = disk_stats

    return stats


def disk_info(vm_drive):
    drive_stats = {
        'truesize': str(vm_drive.truesize),
        'apparentsize': str(vm_drive.apparentsize),
        'readLatency': '0',
        'writeLatency': '0',
        'flushLatency': '0',
        'writtenBytes': '0',
        'writeOps': '0',
        'readOps': '0',
        'readBytes': '0',
        'readRate': '0.0',
        'writeRate': '0.0',
    }
    if isVdsmImage(vm_drive):
        drive_stats['imageID'] = vm_drive.imageID
    elif "GUID" in vm_drive:
        drive_stats['lunGUID'] = vm_drive.GUID
    return drive_stats


def _disk_rate(first_sample, first_index, last_sample, last_index, interval):
    stats = {}

    for name, mode in (("readRate", "rd"), ("writeRate", "wr")):
        first_key = 'block.%d.%s.bytes' % (first_index, mode)
        last_key = 'block.%d.%s.bytes' % (last_index, mode)
        try:
            first_value = first_sample[first_key]
            last_value = last_sample[last_key]
        except KeyError:
            continue
        stats[name] = str((last_value - first_value) / interval)

    return stats


def _disk_latency(first_sample, first_index, last_sample, last_index):
    stats = {}

    for name, mode in (('readLatency', 'rd'),
                       ('writeLatency', 'wr'),
                       ('flushLatency', 'fl')):
        try:
            last_key = "block.%d.%s" % (last_index, mode)
            first_key = "block.%d.%s" % (first_index, mode)
            operations = (last_sample[last_key + ".reqs"] -
                          first_sample[first_key + ".reqs"])
            elapsed_time = (last_sample[last_key + ".times"] -
                            first_sample[first_key + ".times"])
        except KeyError:
            continue
        if operations:
            stats[name] = str(elapsed_time / operations)
        else:
            stats[name] = '0'

    return stats


def _disk_iops_bytes(first_sample, first_index, last_sample, last_index):
    stats = {}

    for name, mode, field in (('readOps', 'rd', 'reqs'),
                              ('writeOps', 'wr', 'reqs'),
                              ('readBytes', 'rd', 'bytes'),
                              ('writtenBytes', 'wr', 'bytes')):
        key = 'block.%d.%s.%s' % (last_index, mode, field)
        try:
            value = last_sample[key]
        except KeyError:
            continue
        stats[name] = str(value)

    return stats


def _usage_percentage(val, interval):
    return 100 * val / interval / 1000 ** 3


def _find_bulk_stats_reverse_map(stats, group):
    name_to_idx = {}
    for idx in six.moves.xrange(stats.get('%s.count' % group, 0)):
        try:
            name = stats['%s.%d.name' % (group, idx)]
        except KeyError:
            # Bulk stats accumulate what they can get, raising errors
            # only in the critical cases. This includes fundamental
            # attributes like names, so count has to be considered
            # an upper bound more like a precise indicator.
            pass
        else:
            name_to_idx[name] = idx
    return name_to_idx


def memory(stats, first_sample, last_sample, interval):
    mem_stats = {}

    if last_sample is not None:

        # If the balloon stats are not available (for some reason) we want to
        # give chance to oVirt GA. This check should be removed once we drop
        # support for OGA completely.
        if 'balloon.available' not in last_sample:
            return

        mem_stats['mem_total'] = str(last_sample.get('balloon.available', 0))
        mem_stats['mem_unused'] = str(last_sample.get('balloon.unused', 0))

        # 'mem_free' used to report the free memory (aka 'mem_unused')
        # plus memory allocated for buffers (aka 'mem_buffers') and
        # caches (aka 'mem_cached'). On host with libvirt at least 4.6.0 and
        # guests with kernel at least 4.16 we can obtain sum of buffers and
        # caches in balloon.disk_caches.
        mem_stats['mem_free'] = str(
            last_sample.get('balloon.unused', 0) +
            last_sample.get('balloon.disk_caches', 0))

    if first_sample is not None and last_sample is not None \
            and interval > 0:
        stats_map = {
            'swap_in': 'balloon.swap_in',
            'swap_out': 'balloon.swap_out',
            'majflt': 'balloon.major_fault',
            'minflt': 'balloon.minor_fault',
        }
        for (k, v) in six.iteritems(stats_map):
            # pylint: disable=round-builtin
            mem_stats[k] = int(round((
                last_sample.get(v, 0) - first_sample.get(v, 0)
            ) / interval))

        # This stat is deprecated
        mem_stats['pageflt'] = mem_stats['majflt'] + mem_stats['minflt']

    stats['memoryStats'] = mem_stats


@contextlib.contextmanager
def _skip_if_missing_stats(vm_obj):
    """
    Depending on the VM state, some exceptions while accessing
    the bulk stats samples are to be expected, and harmless.
    This context manager swallows those and let the others
    bubble up.
    """
    try:
        yield
    except KeyError as exc:
        if not vm_obj.monitorable:
            # If a VM is migration destination,
            # libvirt doesn't give any disk stat.
            pass
        else:
            _log.warning('Missing stat: %s for vm %s', str(exc), vm_obj.id)
