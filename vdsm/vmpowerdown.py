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
import libvirt

from vdsm import utils
from vdsm.define import doneCode, errCode


class VmPowerDown(object):
    """
    Base class for the VmShutdown and VmReboot commands.
    Derived classes must provide the guestAgentCallback and acpiCallback
    methods and returnMsg property.
    """
    def __init__(self, vm, delay, message, timeout, event):
        """
        :param vm:      Vm undergoing power-down action
        :param delay:   Graceful timeout for the user to close his applications
                        (in seconds). During this time no action is taken.
        :param message: Message to show the user.
        :param timeout: Timeout for each power-down method (guestAgent, acpi)
                        until it is considered unsuccessful and the callback
                        chain should try another alternative.
        :param event:   Event object used to detect successful power-down.
        """
        self.vm = vm
        self.chain = utils.CallbackChain()
        self.delay = delay
        self.message = message
        self.timeout = timeout
        self.event = event

        # first try agent
        if vm.guestAgent and vm.guestAgent.isResponsive():
            self.chain.addCallback(self.guestAgentCallback)

        # then acpi if enabled
        if utils.tobool(vm.conf.get('acpiEnable', 'true')):
            self.chain.addCallback(self.acpiCallback)

    def start(self):
        # are there any available methods for power-down?
        if self.chain.callbacks:
            # flag for successful power-down event detection
            # this flag is common for both shutdown and reboot workflows
            # because we want to exit the CallbackChain in case either
            # of them happens
            self.event.clear()

            self.chain.start()
            return {'status': {'code': doneCode['code'],
                               'message': self.returnMsg}}
        else:
            # No tools, no ACPI
            return {
                'status': {
                    'code': errCode['exist']['status']['code'],
                    'message': 'VM without ACPI or active oVirt guest agent. '
                               'Try Forced Shutdown.'}}


class VmShutdown(VmPowerDown):
    returnMsg = 'Machine shutting down'

    def guestAgentCallback(self):
        self.vm.guestAgent.desktopShutdown(self.delay, self.message, False)
        return self.event.wait(self.delay + self.timeout)

    def acpiCallback(self):
        self.vm._dom.shutdownFlags(libvirt.VIR_DOMAIN_SHUTDOWN_ACPI_POWER_BTN)
        return self.event.wait(self.timeout)


class VmReboot(VmPowerDown):
    returnMsg = 'Machine rebooting'

    def guestAgentCallback(self):
        self.vm.guestAgent.desktopShutdown(self.delay, self.message, True)
        return self.event.wait(self.delay + self.timeout)

    def acpiCallback(self):
        self.vm._dom.reboot(libvirt.VIR_DOMAIN_REBOOT_ACPI_POWER_BTN)
        return self.event.wait(self.timeout)
