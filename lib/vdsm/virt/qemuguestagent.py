# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from collections import defaultdict
import copy
import ipaddress
import json
import libvirt
import re
import threading
import time

from vdsm import utils
from vdsm import executor
from vdsm import taskset
from vdsm.common import exception
from vdsm.common.time import monotonic_time
from vdsm.config import config
from vdsm.virt import periodic
from vdsm.virt import guestagenthelpers
from vdsm.virt import virdomain
from vdsm.virt import vmstatus

from libvirt import \
    VIR_DOMAIN_GUEST_INFO_USERS,  \
    VIR_DOMAIN_GUEST_INFO_OS, \
    VIR_DOMAIN_GUEST_INFO_TIMEZONE, \
    VIR_DOMAIN_GUEST_INFO_HOSTNAME, \
    VIR_DOMAIN_GUEST_INFO_FILESYSTEM, \
    VIR_DOMAIN_GUEST_INFO_DISKS

"""
Periodic scheduler that polls QEMU Guest Agent for information.
"""


_QEMU_ACTIVE_USERS_COMMAND = 'guest-get-users'
_QEMU_DEVICES_COMMAND = 'guest-get-devices'
_QEMU_GUEST_INFO_COMMAND = 'guest-info'
_QEMU_HOST_NAME_COMMAND = 'guest-get-host-name'
_QEMU_NETWORK_INTERFACES_COMMAND = 'guest-network-get-interfaces'
_QEMU_OSINFO_COMMAND = 'guest-get-osinfo'
_QEMU_TIMEZONE_COMMAND = 'guest-get-timezone'
_QEMU_FSINFO_COMMAND = 'guest-get-fsinfo'
_QEMU_DISKS_COMMAND = 'guest-get-disks'
_QEMU_VCPUS_COMMAND = 'guest-get-vcpus'

_HOST_NAME_FIELD = 'host-name'
_OS_ID_FIELD = 'id'
_TIMEZONE_OFFSET_FIELD = 'offset'
_TIMEZONE_ZONE_FIELD = 'zone'
_FS_DISK_FIELD = 'disk'
_FS_DISK_DEVICE_FIELD = 'dev'
_FS_DISK_SERIAL_FIELD = 'serial'

_GUEST_OS_WINDOWS = 'mswindows'

_WORKERS = config.getint('guest_agent', 'periodic_workers')
_TASK_PER_WORKER = config.getint('guest_agent', 'periodic_task_per_worker')
_TASKS = _WORKERS * _TASK_PER_WORKER
_MAX_WORKERS = config.getint('guest_agent', 'max_workers')

_COMMAND_TIMEOUT = config.getint('guest_agent', 'qga_command_timeout')
_HOTPLUG_CHECK_PERIOD = 10
_INITIAL_INTERVAL = config.getint('guest_agent', 'qga_initial_info_interval')
_TASK_TIMEOUT = config.getint('guest_agent', 'qga_task_timeout')
_THROTTLING_INTERVAL = 60


# These values are needed internaly and are not defined by libvirt. Beware
# that the values cannot colide with those for VIR_DOMAIN_GUEST_INFO_*
# constants!
VDSM_GUEST_INFO = (1 << 30)
VDSM_GUEST_INFO_NETWORK = (1 << 31)
VDSM_GUEST_INFO_DRIVERS = (1 << 32)
VDSM_GUEST_INFO_CPUS = (1 << 33)

_QEMU_COMMANDS = {
    VDSM_GUEST_INFO_CPUS: _QEMU_VCPUS_COMMAND,
    VDSM_GUEST_INFO_DRIVERS: _QEMU_DEVICES_COMMAND,
    VDSM_GUEST_INFO_NETWORK: _QEMU_NETWORK_INTERFACES_COMMAND,
    VIR_DOMAIN_GUEST_INFO_DISKS: _QEMU_DISKS_COMMAND,
    VIR_DOMAIN_GUEST_INFO_FILESYSTEM: _QEMU_FSINFO_COMMAND,
    VIR_DOMAIN_GUEST_INFO_HOSTNAME: _QEMU_HOST_NAME_COMMAND,
    VIR_DOMAIN_GUEST_INFO_OS: _QEMU_OSINFO_COMMAND,
    VIR_DOMAIN_GUEST_INFO_TIMEZONE: _QEMU_TIMEZONE_COMMAND,
    VIR_DOMAIN_GUEST_INFO_USERS: _QEMU_ACTIVE_USERS_COMMAND,
}

_QEMU_COMMAND_PERIODS = {
    VDSM_GUEST_INFO:
        config.getint('guest_agent', 'qga_info_period'),
    VDSM_GUEST_INFO_CPUS:
        config.getint('guest_agent', 'qga_cpu_info_period'),
    VDSM_GUEST_INFO_DRIVERS:
        config.getint('guest_agent', 'qga_sysinfo_period'),
    VDSM_GUEST_INFO_NETWORK:
        config.getint('guest_agent', 'qga_sysinfo_period'),
    VIR_DOMAIN_GUEST_INFO_DISKS:
        config.getint('guest_agent', 'qga_disk_info_period'),
    VIR_DOMAIN_GUEST_INFO_FILESYSTEM:
        config.getint('guest_agent', 'qga_disk_info_period'),
    VIR_DOMAIN_GUEST_INFO_HOSTNAME:
        config.getint('guest_agent', 'qga_sysinfo_period'),
    VIR_DOMAIN_GUEST_INFO_OS:
        config.getint('guest_agent', 'qga_sysinfo_period'),
    VIR_DOMAIN_GUEST_INFO_TIMEZONE:
        config.getint('guest_agent', 'qga_sysinfo_period'),
    VIR_DOMAIN_GUEST_INFO_USERS:
        config.getint('guest_agent', 'qga_active_users_period'),
}

_DISK_DEVICE_RE = re.compile('^(/dev/[hsv]d[a-z]+)[0-9]+$')

CHANNEL_CONNECTED = \
    libvirt.VIR_CONNECT_DOMAIN_EVENT_AGENT_LIFECYCLE_STATE_CONNECTED
CHANNEL_DISCONNECTED = \
    libvirt.VIR_CONNECT_DOMAIN_EVENT_AGENT_LIFECYCLE_STATE_DISCONNECTED
CHANNEL_UNKNOWN = -1


def channel_state_to_str(state):
    """
    Turn state constant defined above (and in libvirt) to string
    representation. The strings match textual representation returned by
    libvirt in domain XML, but this is not a requirement and is only for
    convenience.
    """
    # NOTE: This function must handle invalid values properly because it can
    # receive unsanitized input!
    if state == CHANNEL_CONNECTED:
        return 'connected'
    elif state == CHANNEL_DISCONNECTED:
        return 'disconnected'
    else:
        return 'UNKNOWN'


@virdomain.expose("guestInfo", "interfaceAddresses", "guestVcpus")
class QemuGuestAgentDomain(object):
    """Wrapper object exposing libvirt API."""
    def __init__(self, vm):
        self._vm = vm

    def interfaceAddresses(self, source):
        """Method stub to make pylint happy."""
        raise NotImplementedError("method stub")

    def guestInfo(self, types, flags):
        """Method stub to make pylint happy."""
        raise NotImplementedError("method stub")

    def guestVcpus(self, flags=0):
        """Method stub to make pylint happy."""
        raise NotImplementedError("method stub")


class QemuGuestAgentPoller(object):

    def __init__(self, cif, log, scheduler):
        self._cif = cif
        self.log = log
        self._scheduler = scheduler
        self._executor = executor.Executor(name="qgapoller",
                                           workers_count=_WORKERS,
                                           max_tasks=_TASKS,
                                           scheduler=scheduler,
                                           max_workers=_MAX_WORKERS)
        self._operations = []
        self._capabilities_lock = threading.Lock()
        self._capabilities = {}
        self._guest_info_lock = threading.Lock()
        self._guest_info = defaultdict(dict)
        self._last_failure_lock = threading.Lock()
        self._last_failure = defaultdict(lambda: 0)
        self._last_check_lock = threading.Lock()
        # Key is tuple (vm_id, command)
        self._last_check = defaultdict(lambda: 0)
        self._channel_state = defaultdict(lambda: CHANNEL_UNKNOWN)
        self._channel_state_hint = defaultdict(lambda: CHANNEL_UNKNOWN)
        self._channel_state_lock = threading.Lock()
        self._initial_interval = config.getint(
            'guest_agent', 'qga_initial_info_interval')
        self.log.info('Using libvirt for querying QEMU-GA')

    def start(self):
        if not config.getboolean('guest_agent', 'enable_qga_poller'):
            self.log.info('Not starting QEMU-GA poller. It is disabled in'
                          ' configuration')
            return
        self._operation = periodic.Operation(
            self._poller,
            config.getint('guest_agent', 'qga_polling_period'),
            self._scheduler,
            timeout=_TASK_TIMEOUT,
            executor=self._executor,
            exclusive=True)
        self.log.info("Starting QEMU-GA poller")
        self._executor.start()
        self._operation.start()

    def stop(self):
        """"Stop the QEMU-GA poller execution"""
        self.log.info("Stopping QEMU-GA poller")
        self._operation.stop()

    def _empty_caps(self):
        """ Dictionary for storing capabilities """
        return {
            'version': None,
            'commands': [],
        }

    def get_caps(self, vm_id):
        with self._capabilities_lock:
            caps = self._capabilities.get(vm_id, None)
            if caps is None:
                caps = self._empty_caps()
                self._capabilities[vm_id] = caps
            # Return a copy so the caller has a stable representation
            return utils.picklecopy(caps)

    def update_caps(self, vm_id, caps):
        if caps is None:
            caps = self._empty_caps()
        if self.get_caps(vm_id) != caps:
            self.log.info(
                "New QEMU-GA capabilities for vm_id=%s, qemu-ga=%s,"
                " commands=%r", vm_id, caps['version'], caps['commands'])
            with self._capabilities_lock:
                self._capabilities[vm_id] = caps

    def get_guest_info(self, vm_id):
        with self._guest_info_lock:
            # Return a copy so the caller has a stable representation
            return utils.picklecopy(self._guest_info.get(vm_id, None))

    def update_guest_info(self, vm_id, info):
        with self._guest_info_lock:
            self._guest_info[vm_id].update(info)

    def last_failure(self, vm_id):
        return self._last_failure[vm_id]

    def reset_failure(self, vm_id):
        with self._last_failure_lock:
            if vm_id in self._last_failure:
                del self._last_failure[vm_id]

    def set_failure(self, vm_id):
        with self._last_failure_lock:
            self._last_failure[vm_id] = monotonic_time()

    def last_check(self, vm_id, command):
        return self._last_check[(vm_id, command)]

    def set_last_check(self, vm_id, command, time=None):
        if time is None:
            time = monotonic_time()
        with self._last_check_lock:
            self._last_check[(vm_id, None)] = time
            self._last_check[(vm_id, command)] = time

    def is_active(self, vm_id):
        last = self.last_check(vm_id, None)
        failed = self.last_failure(vm_id)
        if last > 0 and last > failed:
            return True
        return False

    def channel_state_changed(self, vm_id, state, reason):
        """
        Function used to notify the poller about change in state of guest
        agent channel. Outside the object, this method should be used only in
        response to libvirt events. In other situations use
        channel_state_hint().
        """
        prev_state = self._channel_state[vm_id]
        self.log.info(
            'Channel state for vm_id=%s changed from=%s(%r) to=%s(%r)',
            vm_id,
            channel_state_to_str(self._channel_state[vm_id]),
            prev_state,
            channel_state_to_str(state),
            state)
        if not isinstance(state, int):
            raise TypeError('Expected int for "state" argument')
        if state not in (CHANNEL_CONNECTED, CHANNEL_DISCONNECTED):
            raise ValueError('Invalid state value "%r"' % state)
        with self._channel_state_lock:
            self._channel_state[vm_id] = state
        if prev_state != state and state == CHANNEL_CONNECTED:
            # Clean failures on disconnected -> connected transition
            self.reset_failure(vm_id)

    def channel_state_hint(self, vm_id, state):
        """
        Give a hint about channel state. This method should be used when the
        source of the hint is not a libvirt event.
        """
        if not isinstance(state, str):
            raise TypeError('Expected str for "state" argument')
        if state == 'connected':
            int_state = CHANNEL_CONNECTED
        elif state == 'disconnected':
            int_state = CHANNEL_DISCONNECTED
        else:
            raise ValueError('Invalid state value "%r"' % state)
        self.log.debug('Stored channel state hint for vm_id=%s, hint=%s',
                       vm_id, state)
        self._channel_state_hint[vm_id] = int_state

    def call_qga_command(self, vm, command, args=None):
        """
        Execute QEMU-GA command and return result as dict or None on error

        command   the command to execute (string)
        args      arguments to the command (dict) or None
        """
        # First make sure the command is supported by QEMU-GA
        if command != _QEMU_GUEST_INFO_COMMAND:
            caps = self.get_caps(vm.id)
            if command not in caps['commands']:
                self.log.debug(
                    'Not sending QEMU-GA command \'%s\' to vm_id=\'%s\','
                    ' command is not supported', command, vm.id)
                return None
        # See if the agent is connected before sending anything
        if self._channel_state[vm.id] != CHANNEL_CONNECTED:
            self.log.debug(
                'Not sending QEMU-GA command \'%s\' to vm_id=\'%s\','
                ' agent is not connected', command, vm.id)
            return None

        cmd = {'execute': command}
        if args is not None:
            cmd['arguments'] = args
        cmd = json.dumps(cmd)
        try:
            self.log.debug(
                'Calling QEMU-GA command for vm_id=\'%s\', command: %s',
                vm.id, cmd)
            ret = vm.qemu_agent_command(cmd, _COMMAND_TIMEOUT, 0)
            self.log.debug('Call returned: %r', ret)
        except virdomain.NotConnectedError:
            self.log.debug(
                'Not querying QEMU-GA because domain is not running ' +
                'for vm-id=%s', vm.id)
            return None
        except libvirt.libvirtError:
            # Most likely the QEMU-GA is not installed or is unresponsive
            self.set_failure(vm.id)
            return None

        try:
            parsed = json.loads(ret)
        except ValueError:
            self.log.exception(
                'Failed to parse string returned by QEMU-GA: %r', ret)
            return None
        if 'error' in parsed:
            self.log.error('Error received from QEMU-GA: %r', ret)
            return None
        if 'return' not in parsed:
            self.log.error(
                'Invalid response from QEMU-GA: %r', ret)
            return None
        return parsed['return']

    def _on_boot(self, vm, now):
        """
        When VM starts we want to be more aggressive and do some queries
        regardless of the configured periods.
        """
        vm_elapsed_time = time.time() - vm.start_time
        if vm_elapsed_time > _INITIAL_INTERVAL:
            return
        # Check for qemu-ga presence
        caps = self.get_caps(vm.id)
        if caps['version'] is None:
            if vm.isDomainRunning():
                self._qga_capability_check(vm, now)
                caps = self.get_caps(vm.id)
                if caps['version'] is not None:
                    # Finally, the agent is up!
                    self.reset_failure(vm.id)
            else:
                self.log.debug(
                    'Not querying QEMU-GA yet, domain not running')
                return
        # This is a best-effort check for non-local networks. We cannot
        # guarantee the network is up or working, but this should handle most
        # of the cases with simple DHCP configuration.
        local_ifaces = ['lo', 'docker0']
        info = self.get_guest_info(vm.id)
        have_some = False
        for iface in info.get('netIfaces', []):
            if iface['name'] in local_ifaces:
                continue
            for addr in iface['inet'] + iface['inet6']:
                addr = ipaddress.ip_address(addr)
                if not addr.is_loopback and not addr.is_link_local:
                    have_some = True
                    break
            if have_some:
                break
        if not have_some:
            self.update_guest_info(
                vm.id, self._qga_call_network_interfaces(vm))
            self.set_last_check(vm.id, VDSM_GUEST_INFO_NETWORK, now)

    def _poller(self):
        for vm_id, vm_obj in self._cif.getVMs().items():
            now = monotonic_time()
            # Check if there is any state hint to accept/reject
            if self._channel_state_hint[vm_id] != CHANNEL_UNKNOWN:
                # This does not need a lock because we don't care for the
                # small race here. If we accept this hint we don't care for
                # another and if we don't accept this hint we would reject
                # another hint in the next run anyway.
                hint = self._channel_state_hint[vm_id]
                self._channel_state_hint[vm_id] = CHANNEL_UNKNOWN
                hint_accepted = False
                with self._channel_state_lock:
                    # Note that we always prefer information we already have
                    # to make sure we don't lose state changes that come from
                    # events.
                    if self._channel_state[vm_id] == CHANNEL_UNKNOWN:
                        self._channel_state[vm_id] = hint
                        hint_accepted = True
                self.log.debug(
                    '%s channel state hint for vm_id=%s, hint=%r',
                    'Accepted' if hint_accepted else 'Rejected',
                    vm_id, channel_state_to_str(hint))

            # Ensure we know guest agent's capabilities
            self._on_boot(vm_obj, now)
            if not self._runnable_on_vm(vm_obj):
                self.log.debug(
                    'Skipping vm-id=%s in this run and not querying QEMU-GA',
                    vm_id)
                continue
            caps = self.get_caps(vm_id)
            # Update capabilities -- if we just got the caps above then this
            # will fall through
            if (now - self.last_check(vm_id, VDSM_GUEST_INFO)
                    >= _QEMU_COMMAND_PERIODS[VDSM_GUEST_INFO]):
                self._qga_capability_check(vm_obj, now)
                caps = self.get_caps(vm_id)
            if caps['version'] is None:
                # If we don't know about the agent there is no reason to
                # proceed any further
                continue
            # Update guest info
            types = 0
            for command in _QEMU_COMMANDS.keys():
                if _QEMU_COMMANDS[command] not in caps['commands']:
                    continue
                after_hotplug = \
                    (command == VIR_DOMAIN_GUEST_INFO_FILESYSTEM or
                     command == VIR_DOMAIN_GUEST_INFO_DISKS) and \
                    vm_obj.last_disk_hotplug() is not None and \
                    (now - vm_obj.last_disk_hotplug() >=
                        _HOTPLUG_CHECK_PERIOD) and \
                    (self.last_check(vm_id, command) <
                        vm_obj.last_disk_hotplug() + _HOTPLUG_CHECK_PERIOD)
                if now - self.last_check(vm_id, command) \
                        < _QEMU_COMMAND_PERIODS[command] and \
                        not after_hotplug:
                    continue
                # Commands that have special handling go here
                if command == VDSM_GUEST_INFO_CPUS:
                    self.update_guest_info(
                        vm_id, self._qga_call_get_vcpus(vm_obj))
                    self.set_last_check(vm_id, command, now)
                elif command == VDSM_GUEST_INFO_DRIVERS:
                    self.update_guest_info(
                        vm_id, self._qga_call_get_devices(vm_obj))
                    self.set_last_check(vm_id, command, now)
                elif command == VDSM_GUEST_INFO_NETWORK:
                    self.update_guest_info(
                        vm_id, self._qga_call_network_interfaces(vm_obj))
                    self.set_last_check(vm_id, command, now)
                # Commands handled by libvirt guestInfo() go here
                else:
                    types |= command
            if types == 0:
                # Nothing to do
                continue
            info = self._libvirt_get_guest_info(vm_obj, types)
            if info is None:
                self.log.debug('Failed to query QEMU-GA for vm=%s', vm_id)
                self.set_failure(vm_id)
            else:
                self.update_guest_info(vm_id, info)
                for command in _QEMU_COMMANDS.keys():
                    if types & command:
                        self.set_last_check(vm_id, command, now)
        # Remove stale info
        self._cleanup()

    def _libvirt_get_guest_info(self, vm, types):
        guest_info = {}
        self.log.debug(
            'libvirt: fetching guest info vm_id=%r types=%s',
            vm.id, bin(types))
        try:
            # Note: The timeout here is really for each command that will be
            #       invoked and not for the guestInfo() call as whole.
            with vm.qga_context(_COMMAND_TIMEOUT):
                info = QemuGuestAgentDomain(vm).guestInfo(types, 0)
        except (exception.NonResponsiveGuestAgent, libvirt.libvirtError) as e:
            self.log.info('Failed to get guest info for vm=%s, error: %s',
                          vm.id, e)
            self.set_failure(vm.id)
            return {}
        except virdomain.NotConnectedError:
            self.log.debug(
                'Not querying QEMU-GA because domain is not running ' +
                'for vm-id=%s', vm.id)
            return {}
        # Disks Info
        if 'disk.count' in info:
            guest_info.update(self._libvirt_disks(info))
        # Filesystem Info
        # We only set disk mapping here if we do not have _QEMU_DISKS_COMMAND
        if 'fs.count' in info:
            caps = self.get_caps(vm.id)
            store_disk_mapping = _QEMU_DISKS_COMMAND not in caps['commands']
            guest_info.update(self._libvirt_fsinfo(info, store_disk_mapping))
        # Hostname
        if 'hostname' in info:
            guest_info['guestName'] = info['hostname']
            guest_info['guestFQDN'] = info['hostname']
        # OS Info
        if 'os.id' in info:
            if info.get('os.id') == _GUEST_OS_WINDOWS:
                guest_info.update(
                    guestagenthelpers.translate_windows_osinfo(info))
            else:
                guest_info.update(
                    guestagenthelpers.translate_linux_osinfo(info))
            self.fake_apps_list(
                vm.id, info['os.id'], info['os.kernel-release'])
        # Timezone
        if 'timezone.offset' in info:
            guest_info['guestTimezone'] = {
                'offset': info['timezone.offset'] // 60,
                'zone': info.get('timezone.name', 'unknown'),
            }
        # User Info
        if 'user.count' in info:
            users = []
            for i in range(info['user.count']):
                prefix = 'user.%d' % i
                if info.get(prefix + '.domain', '') != '':
                    users.append(
                        info[prefix + '.name'] + '@' +
                        info[prefix + '.domain'])
                else:
                    users.append(info[prefix + '.name'])
            guest_info['username'] = ', '.join(users)
        return guest_info

    def _libvirt_disks(self, info):
        mapping = {}
        for di in range(info.get('disk.count')):
            disk_prefix = 'disk.{:d}.'.format(di)
            if ((disk_prefix + 'name') in info and
                    (disk_prefix + 'serial') in info):
                serial = info[disk_prefix + 'serial']
                mapping[serial] = {'name': info[disk_prefix + 'name']}
        return {'diskMapping': mapping}

    def _libvirt_fsinfo(self, info, store_disk_mapping=True):
        disks = []
        mapping = {}
        for i in range(info.get('fs.count', 0)):
            prefix = 'fs.{:d}.'.format(i)
            try:
                fsinfo = guestagenthelpers.translate_fsinfo(info, i)
            except ValueError:
                self.log.warning(
                    'Invalid message returned to call \'%s\': %r',
                    _QEMU_FSINFO_COMMAND, info)
                continue
            # Skip stats with missing info. This is e.g. the case of System
            # Reserved volumes on Windows.
            if fsinfo['total'] != '' and fsinfo['used'] != '':
                disks.append(fsinfo)
            # Store disk mapping
            if not store_disk_mapping:
                continue
            for di in range(info.get(prefix + 'disk.count')):
                disk_prefix = '{}disk.{:d}.'.format(prefix, di)
                if (disk_prefix + 'serial') in info and \
                        (disk_prefix + 'device') in info:
                    dev = info[disk_prefix + 'device']
                    m = _DISK_DEVICE_RE.match(dev)
                    if m is not None:
                        dev = m.group(1)
                        self.log.debug(
                            'Stripping partition number: %s -> %s',
                            info[disk_prefix + 'device'], dev)
                    mapping[info[disk_prefix + 'serial']] = {'name': dev}
        if store_disk_mapping:
            return {'disksUsage': disks, 'diskMapping': mapping}
        else:
            return {'disksUsage': disks}

    def fake_apps_list(self, vm_id, os_id=None, kernel_release=None):
        """ Create fake appsList entry in guest info """
        apps = []
        caps = self.get_caps(vm_id)
        if os_id is not None and os_id != _GUEST_OS_WINDOWS:
            apps.append('kernel-%s' % kernel_release)
        if caps is not None and caps['version'] is not None:
            apps.append('qemu-guest-agent-%s' % caps['version'])
        guest_info = {
            'appsList': tuple(apps),
        }
        self.update_guest_info(vm_id, guest_info)

    def _cleanup(self):
        """
        This method is meant to be run periodically to clean up stale
        information about VMs that no longer exist. We don't collect too much
        information, but we should not occupy the memory indefinitely.

        Simple one-shot "unregister" method would not be reliable due to races.
        If the operation for VM that is being unregistered is already scheduled
        the information could reappear. Hence the reason for periodic cleaner.
        """
        removed = set()
        vm_container = self._cif.vmContainer
        with self._capabilities_lock:
            for vm_id in copy.copy(self._capabilities):
                if vm_id not in vm_container:
                    del self._capabilities[vm_id]
                    removed.add(vm_id)
        with self._guest_info_lock:
            for vm_id in copy.copy(self._guest_info):
                if vm_id not in vm_container:
                    del self._guest_info[vm_id]
                    removed.add(vm_id)
        with self._last_failure_lock:
            for vm_id in copy.copy(self._last_failure):
                if vm_id not in vm_container:
                    del self._last_failure[vm_id]
                    removed.add(vm_id)
        with self._last_check_lock:
            for vm_id, command in copy.copy(self._last_check):
                if vm_id not in vm_container:
                    del self._last_check[(vm_id, command)]
                    removed.add(vm_id)
        with self._channel_state_lock:
            for vm_id in copy.copy(self._channel_state):
                if vm_id not in vm_container:
                    del self._channel_state[vm_id]
                    removed.add(vm_id)
        for vm_id in copy.copy(self._channel_state_hint):
            if vm_id not in vm_container:
                del self._channel_state_hint[vm_id]
                removed.add(vm_id)
        if removed:
            self.log.debug('Cleaned up old data for VMs: %s', removed)

    def _runnable_on_vm(self, vm):
        last_failure = self.last_failure(vm.id)
        if (monotonic_time() - last_failure) < _THROTTLING_INTERVAL:
            return False
        if not vm.isDomainRunning():
            return False
        if self._channel_state[vm.id] != CHANNEL_CONNECTED:
            return False
        return True

    def _qga_capability_check(self, vm, now=None):
        """
        This check queries information about installed QEMU Guest Agent.
        What interests us the most is the list of supported commands.

        This cannot be a one-time check and we need periodic task for this.
        The capabilities can change duringe the life-time of the VM. When
        QEMU-GA is installed, upgraded or removed this will change the list of
        available Commands and we definitely don't want the user to start &
        stop the VM.
        """
        caps = self._empty_caps()
        ret = self.call_qga_command(vm, _QEMU_GUEST_INFO_COMMAND)
        if ret is not None:
            caps['version'] = ret['version']
            caps['commands'] = set(
                [c['name'] for c in ret['supported_commands']
                    if c['enabled']])
        self.log.debug('QEMU-GA caps (vm_id=%s): %r', vm.id, caps)
        old_caps = self.get_caps(vm.id)
        self.update_caps(vm.id, caps)
        self.set_last_check(vm.id, VDSM_GUEST_INFO, now)
        info = self.get_guest_info(vm.id)
        if info is None or 'appsList' not in info:
            self.fake_apps_list(vm.id)
        # Change state if it is the first time we see qemu-ga
        new_caps = old_caps['version'] is None and \
            caps['version'] is not None
        guest_starting = vm.guestAgent.guestStatus in (
            None, vmstatus.POWERING_UP, vmstatus.REBOOT_IN_PROGRESS)
        if new_caps and guest_starting:
            # Qemu-ga is running so the guest has to be already up
            vm.guestAgent.guestStatus = vmstatus.UP

    def _qga_call_network_interfaces(self, vm):
        """
        Get the information about network interfaces. There is a libvirt call
        around the QEMU-GA command that we can use.
        """
        # NOTE: The field guestIPs is not used in oVirt Engine since 4.2
        #       so don't even bother filling it.
        guest_info = {'netIfaces': [], 'guestIPs': ''}
        interfaces = {}
        try:
            self.log.debug('Requesting NIC info for vm=%s', vm.id)
            with vm.qga_context(_COMMAND_TIMEOUT):
                interfaces = QemuGuestAgentDomain(vm).interfaceAddresses(
                    libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT)
        except (exception.NonResponsiveGuestAgent, libvirt.libvirtError) as e:
            self.log.info('Failed to get guest info for vm=%s, error: %s',
                          vm.id, e)
            self.set_failure(vm.id)
            return {}
        except virdomain.NotConnectedError:
            self.log.debug(
                'Not querying QEMU-GA because domain is not running ' +
                'for vm-id=%s', vm.id)
            return {}
        for ifname, ifparams in interfaces.items():
            iface = {
                'hw': ifparams.get('hwaddr', ''),
                'inet': [],
                'inet6': [],
                'name': ifname,
            }
            addrs = ifparams.get('addrs')
            for addr in (addrs if addrs is not None else []):
                address = addr.get('addr')
                if address is None:
                    continue
                iftype = addr.get('type')
                if iftype == libvirt.VIR_IP_ADDR_TYPE_IPV4:
                    iface['inet'].append(address)
                elif iftype == libvirt.VIR_IP_ADDR_TYPE_IPV6:
                    iface['inet6'].append(address)
            guest_info['netIfaces'].append(iface)
        return guest_info

    def _qga_call_get_devices(self, vm):
        ret = self.call_qga_command(vm, _QEMU_DEVICES_COMMAND)
        if ret is not None:
            devices = []
            for device in ret:
                id_type = device.get('id', {}).get('type')
                address_type = device.get('address', {}).get('type')
                if id_type == 'pci' or address_type == 'pci':
                    d = guestagenthelpers.translate_pci_device(device)
                    # Qemu-ga returns all devices exactly like they exist in
                    # the VM. That means some devices, e.g. storage
                    # controllers, can appear several times in the list. We
                    # don't need to duplicate the info as engine knows exactly
                    # what devices and in what count are in the VM. We just
                    # care about the driver info.
                    if d not in devices:
                        devices.append(d)
                else:
                    self.log.debug('Skipping unknown device: %r', device)
            return {'pci_devices': devices}
        else:
            self.set_failure(vm.id)
            return {}

    def _qga_call_get_vcpus(self, vm):
        try:
            self.log.debug('Requesting guest CPU info for vm=%s', vm.id)
            with vm.qga_context(_COMMAND_TIMEOUT):
                vcpus = QemuGuestAgentDomain(vm).guestVcpus()
        except (exception.NonResponsiveGuestAgent, libvirt.libvirtError) as e:
            self.log.info('Failed to get guest CPU info for vm=%s, error: %s',
                          vm.id, e)
            self.set_failure(vm.id)
            return {}
        except virdomain.NotConnectedError:
            self.log.debug(
                'Not querying QEMU-GA for guest CPU info because domain'
                'is not running for vm-id=%s', vm.id)
            return {}
        if vcpus is None:
            self.log.info('Guest CPU count was not returned for vm=%s', vm.id)
            return {}
        if 'online' in vcpus:
            count = len(taskset.cpulist_parse(vcpus['online']))
        else:
            count = -1
        return {'guestCPUCount': count}
