#
# Copyright 2017-2020 Red Hat, Inc.
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
Periodic scheduler that polls QEMU Guest Agent for information.
"""

from collections import defaultdict
import copy
import json
import libvirt
import re
import six
import threading
import time

from vdsm import utils
from vdsm import executor
from vdsm.common.time import monotonic_time
from vdsm.config import config
from vdsm.virt import periodic
from vdsm.virt import guestagenthelpers
from vdsm.virt import virdomain

_QEMU_ACTIVE_USERS_COMMAND = 'guest-get-users'
_QEMU_DEVICES_COMMAND = 'guest-get-devices'
_QEMU_GUEST_INFO_COMMAND = 'guest-info'
_QEMU_HOST_NAME_COMMAND = 'guest-get-host-name'
_QEMU_NETWORK_INTERFACES_COMMAND = 'guest-network-get-interfaces'
_QEMU_OSINFO_COMMAND = 'guest-get-osinfo'
_QEMU_TIMEZONE_COMMAND = 'guest-get-timezone'
_QEMU_FSINFO_COMMAND = 'guest-get-fsinfo'

_HOST_NAME_FIELD = 'host-name'
_OS_ID_FIELD = 'id'
_TIMEZONE_OFFSET_FIELD = 'offset'
_TIMEZONE_ZONE_FIELD = 'zone'
_FS_DISK_FIELD = 'disk'
_FS_DISK_DEVICE_FIELD = 'dev'
_FS_DISK_SERIAL_FIELD = 'serial'

_GUEST_OS_LINUX = 'linux'
_GUEST_OS_WINDOWS = 'mswindows'

_WORKERS = config.getint('guest_agent', 'periodic_workers')
_TASK_PER_WORKER = config.getint('guest_agent', 'periodic_task_per_worker')
_TASKS = _WORKERS * _TASK_PER_WORKER
_MAX_WORKERS = config.getint('guest_agent', 'max_workers')

_COMMAND_TIMEOUT = config.getint('guest_agent', 'qga_command_timeout')
_INITIAL_INTERVAL = config.getint('guest_agent', 'qga_initial_info_interval')
_TASK_TIMEOUT = config.getint('guest_agent', 'qga_task_timeout')
_THROTTLING_INTERVAL = 60

# TODO: Remove the try-except when we switch to newer libvirt. The constants
#       are only available in 5.9.0 and newer.
try:
    from libvirt import \
        VIR_DOMAIN_GUEST_INFO_USERS,  \
        VIR_DOMAIN_GUEST_INFO_OS, \
        VIR_DOMAIN_GUEST_INFO_TIMEZONE, \
        VIR_DOMAIN_GUEST_INFO_HOSTNAME, \
        VIR_DOMAIN_GUEST_INFO_FILESYSTEM
    _USE_LIBVIRT = True
    _LIBVIRT_EXPOSED = ["guestInfo", "interfaceAddresses"]
except ImportError:
    VIR_DOMAIN_GUEST_INFO_USERS = (1 << 0)
    VIR_DOMAIN_GUEST_INFO_OS = (1 << 1)
    VIR_DOMAIN_GUEST_INFO_TIMEZONE = (1 << 2)
    VIR_DOMAIN_GUEST_INFO_HOSTNAME = (1 << 3)
    VIR_DOMAIN_GUEST_INFO_FILESYSTEM = (1 << 4)
    _USE_LIBVIRT = False
    _LIBVIRT_EXPOSED = ["interfaceAddresses"]

# These values are needed internaly and are not defined by libvirt. Beware
# that the values cannot colide with those for VIR_DOMAIN_GUEST_INFO_*
# constants!
VDSM_GUEST_INFO = (1 << 30)
VDSM_GUEST_INFO_NETWORK = (1 << 31)
VDSM_GUEST_INFO_DRIVERS = (1 << 32)

_QEMU_COMMANDS = {
    VDSM_GUEST_INFO_DRIVERS: _QEMU_DEVICES_COMMAND,
    VDSM_GUEST_INFO_NETWORK: _QEMU_NETWORK_INTERFACES_COMMAND,
    VIR_DOMAIN_GUEST_INFO_FILESYSTEM: _QEMU_FSINFO_COMMAND,
    VIR_DOMAIN_GUEST_INFO_HOSTNAME: _QEMU_HOST_NAME_COMMAND,
    VIR_DOMAIN_GUEST_INFO_OS: _QEMU_OSINFO_COMMAND,
    VIR_DOMAIN_GUEST_INFO_TIMEZONE: _QEMU_TIMEZONE_COMMAND,
    VIR_DOMAIN_GUEST_INFO_USERS: _QEMU_ACTIVE_USERS_COMMAND,
}

_QEMU_COMMAND_PERIODS = {
    VDSM_GUEST_INFO:
        config.getint('guest_agent', 'qga_info_period'),
    VDSM_GUEST_INFO_DRIVERS:
        config.getint('guest_agent', 'qga_sysinfo_period'),
    VDSM_GUEST_INFO_NETWORK:
        config.getint('guest_agent', 'qga_sysinfo_period'),
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


@virdomain.expose(*_LIBVIRT_EXPOSED)
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
        self._last_failure = {}
        self._last_check_lock = threading.Lock()
        # Key is tuple (vm_id, command)
        self._last_check = defaultdict(lambda: 0)
        self._initial_interval = config.getint(
            'guest_agent', 'qga_initial_info_interval')
        if _USE_LIBVIRT:
            self._get_guest_info = self._libvirt_get_guest_info
            self.log.info('Using libvirt for querying QEMU-GA')
        else:
            self._get_guest_info = self._qga_get_all_info
            self.log.info('Using direct messages for querying QEMU-GA')

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

    def get_caps(self, vm_id):
        with self._capabilities_lock:
            caps = self._capabilities.get(vm_id, None)
            if caps is None:
                caps = {
                    'version': None,
                    'commands': [],
                }
                self._capabilities[vm_id] = caps
            # Return a copy so the caller has a stable representation
            return utils.picklecopy(caps)

    def update_caps(self, vm_id, caps):
        if self._capabilities.get(vm_id, None) != caps:
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
        return self._last_failure.get(vm_id, None)

    def reset_failure(self, vm_id):
        with self._last_failure_lock:
            self._last_failure[vm_id] = None

    def set_failure(self, vm_id):
        with self._last_failure_lock:
            self._last_failure[vm_id] = monotonic_time()

    def last_check(self, vm_id, command):
        return self._last_check[(vm_id, command)]

    def set_last_check(self, vm_id, command, time=None):
        if time is None:
            time = monotonic_time()
        with self._last_check_lock:
            self._last_check[(vm_id, command)] = time

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

    def _poller(self):
        for vm_id, vm_obj in six.viewitems(self._cif.getVMs()):
            now = monotonic_time()
            vm_elapsed_time = time.time() - vm_obj.start_time
            # Ensure we know guest agent's capabilities
            caps = self.get_caps(vm_id)
            if caps['version'] is None and \
                    vm_elapsed_time < _INITIAL_INTERVAL:
                if vm_obj.isDomainRunning():
                    # Enforce check during VM boot
                    self._qga_capability_check(vm_obj)
                    caps = self.get_caps(vm_id)
                    if caps['version'] is not None:
                        # Finally, the agent is up!
                        self.reset_failure(vm_id)
                        self.set_last_check(vm_id, VDSM_GUEST_INFO, now)
                else:
                    self.log.debug(
                        'Not querying QEMU-GA yet, domain not running')
                    continue
            if not self._runnable_on_vm(vm_obj):
                self.log.debug(
                    'Skipping vm-id=%s in this run and not querying QEMU-GA',
                    vm_id)
                continue
            # Update capabilities -- if we just got the caps above then this
            # will fall through
            if (now - self.last_check(vm_id, VDSM_GUEST_INFO)
                    >= _QEMU_COMMAND_PERIODS[VDSM_GUEST_INFO]):
                self._qga_capability_check(vm_obj)
                caps = self.get_caps(vm_id)
                self.set_last_check(vm_id, VDSM_GUEST_INFO, now)
            if caps['version'] is None:
                # If we don't know about the agent there is no reason to
                # proceed any further
                continue
            # Update guest info
            types = 0
            for command in _QEMU_COMMANDS.keys():
                if _QEMU_COMMANDS[command] not in caps['commands']:
                    continue
                if now - self.last_check(vm_id, command) \
                        < _QEMU_COMMAND_PERIODS[command]:
                    continue
                # Commands that have special handling go here
                if command == VDSM_GUEST_INFO_DRIVERS:
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
            info = self._get_guest_info(vm_obj, types)
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

    def _qga_get_all_info(self, vm, types):
        """
        Get info by calling QEMU-GA directly. Interface emulates the
        libvirt API.
        """
        guest_info = {}
        if types == 0:
            return guest_info
        self.log.debug(
            'qemu-ga: fetching info vm_id=%r types=%s', vm.id, bin(types))
        if types & VIR_DOMAIN_GUEST_INFO_FILESYSTEM:
            guest_info.update(self._qga_call_fsinfo(vm))
        if types & VIR_DOMAIN_GUEST_INFO_HOSTNAME:
            guest_info.update(self._qga_call_hostname(vm))
        if types & VIR_DOMAIN_GUEST_INFO_OS:
            guest_info.update(self._qga_call_osinfo(vm))
        if types & VIR_DOMAIN_GUEST_INFO_TIMEZONE:
            guest_info.update(self._qga_call_timezone(vm))
        if types & VIR_DOMAIN_GUEST_INFO_USERS:
            guest_info.update(self._qga_call_active_users(vm))
        return guest_info

    def _libvirt_get_guest_info(self, vm, types):
        guest_info = {}
        self.log.debug(
            'libvirt: fetching guest info vm_id=%r types=%s',
            vm.id, bin(types))
        # TODO: set libvirt timeout
        try:
            info = QemuGuestAgentDomain(vm).guestInfo(types, 0)
        except libvirt.libvirtError as e:
            self.log.info('Failed to get guest info for vm=%s, error: %s',
                          vm.id, str(e))
            self.set_failure(vm.id)
            return {}
        except virdomain.NotConnectedError:
            self.log.debug(
                'Not querying QEMU-GA because domain is not running ' +
                'for vm-id=%s', vm.id)
            return {}
        # Filesystem Info
        if 'fs.count' in info:
            guest_info.update(self._libvirt_fsinfo(info))
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
                self.fake_apps_list(
                    vm.id, info['os.id'], info['os.kernel-release'])
                guest_info.update(
                    guestagenthelpers.translate_linux_osinfo(info))
        # Timezone
        if 'timezone.offset' in info:
            guest_info['guestTimezone'] = {
                'offset': info['timezone.offset'] // 60,
                'zone': info.get('timezone.name', 'unknown'),
            }
        # User Info
        if info.get('user.count', 0) > 0:
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

    def _libvirt_fsinfo(self, info):
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
            for di in range(info.get(prefix + 'disk.count')):
                disk_prefix = '{}disk.{:d}.'.format(prefix, di)
                if (disk_prefix + 'serial') in info and \
                        (disk_prefix + 'device') in info:
                    mapping[info[disk_prefix + 'serial']] = \
                        {'name': info[disk_prefix + 'device']}
        return {'disksUsage': disks, 'diskMapping': mapping}

    def fake_apps_list(self, vm_id, os_id=None, kernel_release=None):
        """ Create fake appsList entry in guest info """
        guest_info = {}
        if os_id is not None:
            if os_id == _GUEST_OS_WINDOWS:
                guest_info['appsList'] = (
                    'QEMU guest agent',
                )
            else:
                caps = self.get_caps(vm_id)
                if caps is not None and caps['version'] is not None:
                    guest_info['appsList'] = (
                        'kernel-%s' % kernel_release,
                        'qemu-guest-agent-%s' % caps['version'],
                    )
        else:
            caps = self.get_caps(vm_id)
            if caps['version'] is not None:
                guest_info['appsList'] = (
                    'qemu-guest-agent-%s' % caps['version'],
                )
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
        if removed:
            self.log.debug('Cleaned up old data for VMs: %s', removed)

    def _runnable_on_vm(self, vm):
        last_failure = self.last_failure(vm.id)
        if last_failure is not None and \
                (monotonic_time() - last_failure) < _THROTTLING_INTERVAL:
            return False
        return True

    def _qga_call_active_users(self, vm):
        """ Get list of active users from the guest OS """
        def format_user(user):
            if user.get('domain', '') != '':
                return user['user'] + '@' + user['domain']
            else:
                return user['user']
        guest_info = {}
        ret = self.call_qga_command(vm, _QEMU_ACTIVE_USERS_COMMAND)
        if ret is None:
            return {}
        try:
            users = [format_user(u) for u in ret]
            guest_info['username'] = ', '.join(users)
        except:
            self.log.warning(
                'Invalid message returned to call \'%s\': %r',
                _QEMU_ACTIVE_USERS_COMMAND, ret)
        return guest_info

    def _qga_capability_check(self, vm):
        """
        This check queries information about installed QEMU Guest Agent.
        What interests us the most is the list of supported commands.

        This cannot be a one-time check and we need periodic task for this.
        The capabilities can change duringe the life-time of the VM. When
        QEMU-GA is installed, upgraded or removed this will change the list of
        available Commands and we definitely don't want the user to start &
        stop the VM.
        """
        caps = {
            'version': None,
            'commands': [],
        }
        ret = self.call_qga_command(vm, _QEMU_GUEST_INFO_COMMAND)
        if ret is not None:
            caps['version'] = ret['version']
            caps['commands'] = set(
                [c['name'] for c in ret['supported_commands']
                    if c['enabled']])
        self.log.debug('QEMU-GA caps (vm_id=%s): %r', vm.id, caps)
        self.update_caps(vm.id, caps)
        info = self.get_guest_info(vm.id)
        if info is None or 'appsList' not in info:
            self.fake_apps_list(vm.id)

    def _qga_call_fsinfo(self, vm):
        """ Get file system information and disk mapping """
        disks = []
        mapping = {}
        ret = self.call_qga_command(vm, _QEMU_FSINFO_COMMAND)
        if ret is None:
            return {}
        for fs in ret:
            try:
                fsinfo = guestagenthelpers.translate_fsinfo(fs)
            except ValueError:
                self.log.warning(
                    'Invalid message returned to call \'%s\': %r',
                    _QEMU_FSINFO_COMMAND, ret)
                continue
            # Skip stats with missing info. This is e.g. the case of System
            # Reserved volumes on Windows.
            if fsinfo['total'] != '' and fsinfo['used'] != '':
                disks.append(fsinfo)
            if _FS_DISK_FIELD not in fs:
                continue
            for d in fs[_FS_DISK_FIELD]:
                if _FS_DISK_SERIAL_FIELD in d and \
                        _FS_DISK_DEVICE_FIELD in d:
                    dev = d[_FS_DISK_DEVICE_FIELD]
                    m = _DISK_DEVICE_RE.match(dev)
                    if m is not None:
                        dev = m.group(1)
                        self.log.debug(
                            'Stripping partition number: %s -> %s',
                            d[_FS_DISK_DEVICE_FIELD], dev)
                    mapping[d[_FS_DISK_SERIAL_FIELD]] = {'name': dev}
        return {'disksUsage': disks, 'diskMapping': mapping}

    def _qga_call_hostname(self, vm):
        ret = self.call_qga_command(vm, _QEMU_HOST_NAME_COMMAND)
        if ret is not None:
            if _HOST_NAME_FIELD not in ret:
                self.log.warning(
                    'Invalid message returned to call \'%s\': %r',
                    _QEMU_HOST_NAME_COMMAND, ret)
            else:
                return {'guestName': ret[_HOST_NAME_FIELD],
                        'guestFQDN': ret[_HOST_NAME_FIELD]}
        return {}

    def _qga_call_osinfo(self, vm):
        ret = self.call_qga_command(vm, _QEMU_OSINFO_COMMAND)
        if ret is not None:
            if ret.get(_OS_ID_FIELD) == _GUEST_OS_WINDOWS:
                return guestagenthelpers.translate_windows_osinfo(ret)
            else:
                self.fake_apps_list(vm.id, ret['id'], ret['kernel-release'])
                return guestagenthelpers.translate_linux_osinfo(ret)
        return {}

    def _qga_call_timezone(self, vm):
        ret = self.call_qga_command(vm, _QEMU_TIMEZONE_COMMAND)
        if ret is not None:
            if _TIMEZONE_OFFSET_FIELD not in ret:
                self.log.warning(
                    'Invalid message returned to call \'%s\': %r',
                    _QEMU_TIMEZONE_COMMAND, ret)
            else:
                return {'guestTimezone': {
                    'offset': ret[_TIMEZONE_OFFSET_FIELD] // 60,
                    'zone': ret.get(_TIMEZONE_ZONE_FIELD, 'unknown'),
                }}
        return {}

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
            interfaces = QemuGuestAgentDomain(vm).interfaceAddresses(
                libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT)
        except libvirt.libvirtError:
            self.set_failure(vm.id)
            return {}
        except virdomain.NotConnectedError:
            self.log.debug(
                'Not querying QEMU-GA because domain is not running ' +
                'for vm-id=%s', vm.id)
            return {}
        for ifname, ifparams in six.iteritems(interfaces):
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
                if device.get('address', {}).get('type') == 'pci':
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
