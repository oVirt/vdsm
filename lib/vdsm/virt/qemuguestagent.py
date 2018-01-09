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
import threading

from vdsm import utils
from vdsm import executor
from vdsm.common.time import monotonic_time
from vdsm.config import config
from vdsm.virt import periodic

_QEMU_GUEST_INFO_COMMAND = 'guest-info'

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
        return self._capabilities.get(vm_id, None)

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
        ret = self._qga_poller.call_qga_command(self._vm, 'guest-info')
        if ret is not None:
            caps['version'] = ret['version']
            caps['commands'] = set([
                c['name'] for c in ret['supported_commands'] if c['enabled']])
        self._qga_poller.log.debug('QEMU-GA caps (vm_id=%s): %r',
                                   self._vm.id, caps)
        self._qga_poller.update_caps(self._vm.id, caps)
