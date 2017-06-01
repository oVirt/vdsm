#
# Copyright 2008-2017 Red Hat, Inc.
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

from vdsm.common import response
from vdsm import utils


class VmPowerDown(object):
    """
    Base class for the VmShutdown and VmReboot commands.
    Derived classes must provide the guestAgentCallback and acpiCallback
    methods and returnMsg property.
    """
    returnMsg = 'Machine power down'

    def __init__(self, vm, delay, message, timeout, force, event):
        """
        :param vm:      Vm undergoing power-down action
        :param delay:   Graceful timeout for the user to close his applications
                        (in seconds). During this time no action is taken.
        :param message: Message to show the user.
        :param timeout: Timeout for each power-down method (guestAgent, acpi)
                        until it is considered unsuccessful and the callback
                        chain should try another alternative.
        :param force:   Use forceful power-down if all graceful methods fail?
        :param event:   Event object used to detect successful power-down.
        """
        self.vm = vm
        self.chain = utils.CallbackChain()
        self.delay = delay
        self.message = message
        self.timeout = timeout
        self.event = event

        # first try agent
        if vm.guestAgent.isResponsive():
            self.chain.addCallback(self.guestAgentCallback)

        # then acpi if enabled
        if vm.acpi_enabled():
            self.chain.addCallback(self.acpiCallback)

        if force:
            self.chain.addCallback(self.forceCallback)

    def start(self):
        # are there any available methods for power-down?
        if self.chain.callbacks:
            # flag for successful power-down event detection
            # this flag is common for both shutdown and reboot workflows
            # because we want to exit the CallbackChain in case either
            # of them happens
            self.event.clear()

            self.chain.start()
            return response.success(message=self.returnMsg)
        else:
            # No tools, no ACPI
            return response.error(
                'exist',
                message='VM without ACPI or active oVirt guest agent. '
                        'Try Forced Shutdown.')

    # action callbacks, to be reimplemented

    def guestAgentCallback(self):
        return False

    def acpiCallback(self):
        return False

    def forceCallback(self):
        return False


class VmShutdown(VmPowerDown):
    returnMsg = 'Machine shutting down'

    def guestAgentCallback(self):
        self.vm.guestAgent.desktopShutdown(self.delay, self.message, False)
        return self.event.wait(self.delay + self.timeout)

    def acpiCallback(self):
        if response.is_error(self.vm.acpiShutdown()):
            return False
        return self.event.wait(self.timeout)

    def forceCallback(self):
        self.vm.doDestroy()
        return self.event.wait(self.timeout)


class VmReboot(VmPowerDown):
    returnMsg = 'Machine rebooting'

    def guestAgentCallback(self):
        self.vm.guestAgent.desktopShutdown(self.delay, self.message, True)
        return self.event.wait(self.delay + self.timeout)

    def acpiCallback(self):
        if response.is_error(self.vm.acpiReboot()):
            return False
        return self.event.wait(self.timeout)

    def forceCallback(self):
        # TODO: fix like acpiShutdown
        self.vm._dom.reset(0)
        return self.event.wait(self.timeout)
