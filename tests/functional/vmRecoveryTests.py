#
# Copyright 2014 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from contextlib import contextmanager
import os
import signal
import socket

import vdsm
from vdsm.tool.service import service_start, service_stop
from virt import vmstatus

from testValidation import ValidateRunningAsRoot
from virtTests import VirtTestBase, RunningVm, VDSMConnectionError

from utils import SUCCESS


def customize_vm(ident):
    return {
        'display': 'qxl',
        'vmId': '99999999-fedc-3333-abcd-'.ljust(36, '%i' % ident),
        'vmName': 'testRecoveryVm%02i' % ident}


class VMProxy(object):
    def __init__(self, vdsm, customization):
        self._customization = customization
        self._vdsm = vdsm
        self._vm_id = None
        self._vm = None

    def start(self):
        self._vm = RunningVm(self._vdsm, self._customization,
                             kernelPath="/boot/vmlinuz-%s" % os.uname()[2],
                             initramfsPath='')
        self._vm_id = self._vm.start()
        return self._vm_id

    def stop(self):
        try:
            return self._vm.stop()
        except VDSMConnectionError:
            return SUCCESS  # we're fine already!

    @property
    def id(self):
        return self._vm_id

    def stats(self):
        status, msg, result = self._vdsm.getVmStats(self._vm_id)
        if status != SUCCESS:
            raise VDSMConnectionError(msg)
        else:
            return result

    @property
    def pid(self):
        return int(self.stats()['pid'])


class RecoveryTests(VirtTestBase):
    @contextmanager
    def running_vms(self, num, customizer):
        vms = []
        for i in range(1, num + 1):
            vm = VMProxy(self.vdsm, customizer(i))
            vm.start()
            vms.append(vm)

        for vm in vms:
            self._waitForBoot(vm.id)

        try:
            yield vms
        finally:
            for vm in vms:
                self.assertEqual(vm.stop(), SUCCESS)

    def ensure_vdsm_started(self):
        vdsm.utils.retry(
            self.setUp, expectedException=(socket.error, KeyError), tries=10)

    @ValidateRunningAsRoot
    def test_vm_recovery(self):
        service_start('vdsmd')

        # two VMs:
        # #0 dies:
        #    it goes down and then VDSM restarts; the VM stats
        #    must conserve the exit* fields unchanged across VDSM restarts.
        # #1 disappears:
        #    it goes down while VDSM is down: must be reported as down.
        with self.running_vms(2, customize_vm) as vms:
            os.kill(vms[0].pid, signal.SIGTERM)
            self._waitForShutdown(vms[0].id)

            stats_before = vms[0].stats()

            pid = vms[1].pid
            service_stop('vdsmd')

            os.kill(pid, signal.SIGTERM)

            service_start('vdsmd')
            self.ensure_vdsm_started()

            stats_after = vms[0].stats()
            self.assertEqual(stats_after['status'], vmstatus.DOWN)
            for field in ('status', 'exitReason', 'exitMessage'):
                self.assertEqual(stats_before[field], stats_after[field])

            self.assertEqual(vms[1].stats()['status'], vmstatus.DOWN)
