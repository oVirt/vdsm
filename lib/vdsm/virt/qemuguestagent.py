#
# Copyright 2017 Red Hat, Inc.
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
#
# As [1] says:
#
#   Libvirt does not guarantee any support of direct use of the guest agent. If
#   you don't mind using libvirt-qemu.so, you can use the
#   virDomainQemuAgentCommand API (exposed by virsh qemu-agent-command); but be
#   aware that this is unsupported, and any changes you make to the agent that
#   change state behind libvirt's back may cause libvirt to misbehave.
#
# So let's be careful and use the interface only to gather information and not
# to change state of the guest. There's no evidence (in code or logs) that
# using the interface should taint the guest.
#
# [1] https://wiki.libvirt.org/page/Qemu_guest_agent
import libvirt_qemu
import six
import threading

from vdsm import utils
from vdsm import executor
from vdsm.common.time import monotonic_time
from vdsm.config import config
from vdsm.virt import periodic
from vdsm.virt import guestagenthelpers

_QEMU_ACTIVE_USERS_COMMAND = 'guest-get-users'
_QEMU_GUEST_INFO_COMMAND = 'guest-info'
_QEMU_HOST_NAME_COMMAND = 'guest-get-host-name'
_QEMU_NETWORK_INTERFACES_COMMAND = 'guest-network-get-interfaces'
_QEMU_OSINFO_COMMAND = 'guest-get-osinfo'
_QEMU_TIMEZONE_COMMAND = 'guest-get-timezone'
_QEMU_FSINFO_COMMAND = 'guest-get-fsinfo'
_QEMU_DISKS_COMMAND = 'guest-get-disks'

_HOST_NAME_FIELD = 'host-name'
_OS_ID_FIELD = 'id'
_TIMEZONE_OFFSET_FIELD = 'offset'
_TIMEZONE_ZONE_FIELD = 'zone'
_FS_DISK_FIELD = 'disk'
_FS_DISK_DEVICE_FIELD = 'dev'
_FS_DISK_SERIAL_FIELD = 'serial'
_DISK_ADDRESS = 'address'

_GUEST_OS_LINUX = 'linux'
_GUEST_OS_WINDOWS = 'mswindows'

_WORKERS = config.getint('guest_agent', 'periodic_workers')
_TASK_PER_WORKER = config.getint('guest_agent', 'periodic_task_per_worker')
_TASKS = _WORKERS * _TASK_PER_WORKER
_MAX_WORKERS = config.getint('guest_agent', 'max_workers')

_COMMAND_TIMEOUT = config.getint('guest_agent', 'qga_command_timeout')
_TASK_TIMEOUT = config.getint('guest_agent', 'qga_task_timeout')
_THROTTLING_INTERVAL = 60


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

    def start(self):
        if not config.getboolean('guest_agent', 'enable_qga_poller'):
            self.log.info('Not starting QEMU-GA poller. It is disabled in'
                          ' configuration')
            return

        def per_vm_operation(job, period):
            disp = periodic.VmDispatcher(
                self._cif.getVMs, self._executor,
                lambda vm: job(vm, self),
                _TASK_TIMEOUT)
            return periodic.Operation(
                disp, period, self._scheduler, timeout=_TASK_TIMEOUT,
                executor=self._executor)

        self._operations = [

            periodic.Operation(
                self._cleanup,
                config.getint('guest_agent', 'cleanup_period'),
                self._scheduler, executor=self._executor),

            # Monitor what QEMU-GA offers
            per_vm_operation(
                CapabilityCheck,
                config.getint('guest_agent', 'qga_info_period')),

            # Basic system information
            per_vm_operation(
                SystemInfoCheck,
                config.getint('guest_agent', 'qga_sysinfo_period')),
            per_vm_operation(
                NetworkInterfacesCheck,
                config.getint('guest_agent', 'qga_sysinfo_period')),

            # List of active users
            per_vm_operation(
                ActiveUsersCheck,
                config.getint('guest_agent', 'qga_active_users_period')),

            # Filesystem info and disk mapping
            per_vm_operation(
                DiskInfoCheck,
                config.getint('guest_agent', 'qga_disk_info_period')),
        ]

        self.log.info("Starting QEMU-GA poller")
        self._executor.start()
        for op in self._operations:
            op.start()

    def stop(self):
        """"Stop the QEMU-GA poller execution"""
        self.log.info("Stopping QEMU-GA poller")
        for op in self._operations:
            op.stop()

    def get_caps(self, vm_id):
        with self._capabilities_lock:
            # Return a copy so the caller has a stable representation
            return utils.picklecopy(self._capabilities.get(vm_id, None))

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

    def set_failure(self, vm_id):
        with self._last_failure_lock:
            self._last_failure[vm_id] = monotonic_time()

    def call_qga_command(self, vm, command, args=None):
        """
        Execute QEMU-GA command and return result as dict or None on error

        command   the command to execute (string)
        args      arguments to the command (dict) or None
        """
        # First make sure the command is supported by QEMU-GA
        if command != _QEMU_GUEST_INFO_COMMAND:
            caps = self.get_caps(vm.id)
            if caps is None or command not in caps['commands']:
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
            ret = libvirt_qemu.qemuAgentCommand(vm._dom, cmd,
                                                _COMMAND_TIMEOUT, 0)
            self.log.debug('Call returned: %r', ret)
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

    def fake_appsList(self, vm_id, os_info=None):
        """ Create fake appsList entry in guest info """
        guest_info = {}
        if os_info is not None:
            if os_info.get(_OS_ID_FIELD) == _GUEST_OS_WINDOWS:
                guest_info['appsList'] = (
                    'QEMU guest agent',
                )
            else:
                caps = self.get_caps(vm_id)
                if caps is not None and caps['version'] is not None:
                    guest_info['appsList'] = (
                        'kernel-%s' % os_info["kernel-release"],
                        'qemu-guest-agent-%s' % caps['version'],
                    )
        else:
            caps = self.get_caps(vm_id)
            if caps is not None and caps['version'] is not None:
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
        self.log.debug('Cleaned up old data for VMs: %s', removed)


class _RunnableOnVmGuestAgent(periodic._RunnableOnVm):
    def __init__(self, vm, qga_poller):
        super(_RunnableOnVmGuestAgent, self).__init__(vm)
        self._qga_poller = qga_poller

    @property
    def runnable(self):
        if not self._vm.isDomainReadyForCommands():
            return False
        last_failure = self._qga_poller.last_failure(self._vm.id)
        if last_failure is not None and \
                (monotonic_time() - last_failure) < _THROTTLING_INTERVAL:
            return False
        return True


class ActiveUsersCheck(_RunnableOnVmGuestAgent):
    """
    Get list of active users from the guest OS
    """
    def _execute(self):
        guest_info = {}
        ret = self._qga_poller.call_qga_command(
            self._vm, _QEMU_ACTIVE_USERS_COMMAND)
        if ret is None:
            return
        try:
            users = [self.format_user(u) for u in ret]
            guest_info['username'] = ', '.join(users)
        except:
            self._qga_poller.log.warning(
                'Invalid message returned to call \'%s\': %r',
                _QEMU_ACTIVE_USERS_COMMAND, ret)
        self._qga_poller.update_guest_info(self._vm.id, guest_info)

    def format_user(self, user):
        if user.get('domain', '') != '':
            return user['user'] + '@' + user.get('domain', '')
        else:
            return user['user']


class CapabilityCheck(_RunnableOnVmGuestAgent):
    """
    This check queries information about installed QEMU Guest Agent.
    What interests us the most is the list of supported commands.

    This cannot be a one-time check and we need periodic task for this. The
    capabilities can change duringe the life-time of the VM. When QEMU-GA is
    installed, upgraded or removed this will change the list of available
    commands and we definitely don't want the user to start & stop the VM.
    """
    def _execute(self):
        caps = {
            'version': None,
            'commands': [],
        }
        ret = self._qga_poller.call_qga_command(
            self._vm,
            _QEMU_GUEST_INFO_COMMAND)
        if ret is not None:
            caps['version'] = ret['version']
            caps['commands'] = set([
                c['name'] for c in ret['supported_commands'] if c['enabled']])
        self._qga_poller.log.debug('QEMU-GA caps (vm_id=%s): %r',
                                   self._vm.id, caps)
        self._qga_poller.update_caps(self._vm.id, caps)
        info = self._qga_poller.get_caps(self._vm.id)
        if 'appsList' not in info:
            self._qga_poller.fake_appsList(self._vm.id)


class DiskInfoCheck(_RunnableOnVmGuestAgent):
    """
    Get file system information and disk mapping
    """
    def _execute(self):
        disks = []
        mapping = {}
        has_mapping = False
        ret = self._qga_poller.call_qga_command(
            self._vm, _QEMU_DISKS_COMMAND)
        if ret is not None:
            has_mapping = True
            for disk in ret:
                if _DISK_ADDRESS not in disk:
                    # possibly virtual disk or partition
                    continue
                name = disk.get('name')
                serial = disk[_DISK_ADDRESS].get('serial')
                if name is not None and serial is not None:
                    mapping[serial] = {'name': name}
        ret = self._qga_poller.call_qga_command(
            self._vm, _QEMU_FSINFO_COMMAND)
        if ret is None:
            return
        for fs in ret:
            try:
                fsinfo = guestagenthelpers.translate_fsinfo(fs)
            except ValueError:
                self._qga_poller.log.warning(
                    'Invalid message returned to call \'%s\': %r',
                    _QEMU_FSINFO_COMMAND, ret)
                continue
            # Skip stats with missing info. This is e.g. the case of System
            # Reserved volumes on Windows.
            if fsinfo['total'] != '' and fsinfo['used'] != '':
                disks.append(fsinfo)
            # Skip the rest if we already have disks mapping or there is no
            # info in the guest reply
            if has_mapping or _FS_DISK_FIELD not in fs:
                continue
            for d in fs[_FS_DISK_FIELD]:
                if _FS_DISK_SERIAL_FIELD in d and \
                        _FS_DISK_DEVICE_FIELD in d:
                    mapping[d[_FS_DISK_SERIAL_FIELD]] = \
                        {'name': d[_FS_DISK_DEVICE_FIELD]}
        self._qga_poller.update_guest_info(
            self._vm.id,
            {'disksUsage': disks, 'diskMapping': mapping})


class SystemInfoCheck(_RunnableOnVmGuestAgent):
    """
    Get the information about system configuration that does not change
    too often.
    """
    def _execute(self):
        guest_info = {}

        # Host name
        ret = self._qga_poller.call_qga_command(
            self._vm, _QEMU_HOST_NAME_COMMAND)
        if ret is not None:
            if _HOST_NAME_FIELD not in ret:
                self._qga_poller.log.warning(
                    'Invalid message returned to call \'%s\': %r',
                    _QEMU_HOST_NAME_COMMAND, ret)
            else:
                guest_info['guestName'] = ret[_HOST_NAME_FIELD]
                guest_info['guestFQDN'] = ret[_HOST_NAME_FIELD]

        # OS version and architecture
        ret = self._qga_poller.call_qga_command(self._vm, _QEMU_OSINFO_COMMAND)
        if ret is not None:
            if ret.get(_OS_ID_FIELD) == _GUEST_OS_WINDOWS:
                guest_info.update(
                    guestagenthelpers.translate_windows_osinfo(ret))
            else:
                guest_info.update(
                    guestagenthelpers.translate_linux_osinfo(ret))
            self._qga_poller.fake_appsList(self._vm.id, ret)

        # Timezone
        ret = self._qga_poller.call_qga_command(
            self._vm, _QEMU_TIMEZONE_COMMAND)
        if ret is not None:
            if _TIMEZONE_OFFSET_FIELD not in ret:
                self._qga_poller.log.warning(
                    'Invalid message returned to call \'%s\': %r',
                    _QEMU_TIMEZONE_COMMAND, ret)
            else:
                guest_info['guestTimezone'] = {
                    'offset': ret[_TIMEZONE_OFFSET_FIELD] // 60,
                    'zone': ret.get(_TIMEZONE_ZONE_FIELD, 'unknown'),
                }

        self._qga_poller.update_guest_info(self._vm.id, guest_info)


class NetworkInterfacesCheck(_RunnableOnVmGuestAgent):
    """
    Get the information about network interfaces. There is a libvirt call
    around the QEMU-GA command that we can use. But it still uses the QEMU-GA
    so it makes sense to do all the pre-checks as if we were calling QEMU-GA
    directly.
    """
    def _execute(self):
        caps = self._qga_poller.get_caps(self._vm.id)
        if caps is None or \
                _QEMU_NETWORK_INTERFACES_COMMAND not in caps['commands']:
            self._qga_poller.log.debug(
                'Not querying network interfaces for vm_id=\'%s\'',
                self._vm.id)
            return

        # NOTE: The field guestIPs is not used in oVirt Engine since 4.2
        #       so don't even bother filling it.
        guest_info = {'netIfaces': [], 'guestIPs': ''}
        interfaces = {}
        try:
            interfaces = self._vm._dom.interfaceAddresses(
                libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT)
        except libvirt.libvirtError:
            self._qga_poller.set_failure(self._vm.id)
            return

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
        self._qga_poller.update_guest_info(self._vm.id, guest_info)
