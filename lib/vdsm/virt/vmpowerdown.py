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
from __future__ import division

from vdsm.common import exception
from vdsm.common import response
from vdsm import utils


class VmPowerDown(object):
    """
    Base class for the VmShutdown and VmReboot commands.
    Derived classes must provide the ovirtGuestAgentCallback and acpiCallback
    methods and returnMsg property.
    """
    returnMsg = 'Machine power down'

    def __init__(self, vm, delay, message, timeout, force, event):
        """
        :param vm:      Vm undergoing power-down action
        :param delay:   Graceful timeout for the user to close his applications
                        (in seconds). During this time no action is taken.
        :param message: Message to show the user.
        :param timeout: Timeout for each power-down method (guest agents, acpi)
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

        # first try the agents
        self.chain.addCallback(self.qemuGuestAgentCallback)

        if vm.guestAgent.isResponsive():
            self.chain.addCallback(self.ovirtGuestAgentCallback)

        # then acpi if enabled
        if vm.acpi_enabled():
            self.chain.addCallback(self.acpiCallback)

        if force:
            self.chain.addCallback(self.forceCallback)

    def start(self):
        self.vm.log.info("Starting powerdown")
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

    def qemuGuestAgentCallback(self):
        return False

    def ovirtGuestAgentCallback(self):
        return False

    def acpiCallback(self):
        return False

    def forceCallback(self):
        return False


class VmShutdown(VmPowerDown):
    returnMsg = 'Machine shutting down'

    def qemuGuestAgentCallback(self):
        # TODO: QEMU GA does not support setting delay for shutdown right
        #       now, but it may get this functionality in the future. When
        #       the feature is implemented in the future it should be also
        #       added here.
        self.vm.log.debug("Shutting down with guest agent")
        try:
            self.vm.qemuGuestAgentShutdown()
        except exception.VdsmException:
            self.vm.log.warning("Shutting down with guest agent FAILED")
            return False

        return utils.log_success(
            self.event.wait(self.timeout),
            self.vm.log,
            "Shutting down with guest agent succeeded",
            "Shutting down with guest agent timeout out"
        )

    def ovirtGuestAgentCallback(self):
        self.vm.log.debug("Shutting down with oVirt agent")
        self.vm.guestAgent.desktopShutdown(self.delay, self.message, False)
        return utils.log_success(
            self.event.wait(self.delay + self.timeout),
            self.vm.log,
            "Shutting down with oVirt agent succeeded",
            "Shutting down with oVirt agent timed out"
        )

    def acpiCallback(self):
        self.vm.log.debug("Shutting down with ACPI")
        if response.is_error(self.vm.acpiShutdown()):
            self.vm.log.warning("Shutting down with ACPI FAILED")
            return False

        return utils.log_success(
            self.event.wait(self.timeout),
            self.vm.log,
            "Shutting down with ACPI succeeded",
            "Shutting down with ACPI timed out"
        )

    def forceCallback(self):
        self.vm.log.debug("Shutting down with FORCE")
        self.vm.doDestroy()

        return utils.log_success(
            self.event.wait(self.timeout),
            self.vm.log,
            "Shutting down with FORCE succeeded",
            "Shutting down with FORCE timed out"
        )


class VmReboot(VmPowerDown):
    returnMsg = 'Machine rebooting'

    def qemuGuestAgentCallback(self):
        # TODO: QEMU GA does not support setting delay for reboot right
        #       now, but it may get this functionality in the future. When
        #       the feature is implemented in the future it should be also
        #       added here.
        self.vm.log.debug("Rebooting with guest agent")
        try:
            self.vm.qemuGuestAgentReboot()
        except exception.VdsmException:
            self.vm.log.warning("Rebooting with guest agent FAILED")
            return False

        return utils.log_success(
            self.event.wait(self.timeout),
            self.vm.log,
            "Rebooting with guest agent succeeded",
            "Rebooting with guest agent timed out"
        )

    def ovirtGuestAgentCallback(self):
        self.vm.log.debug("Rebooting with oVirt agent")
        self.vm.guestAgent.desktopShutdown(self.delay, self.message, True)

        return utils.log_success(
            self.event.wait(self.delay + self.timeout),
            self.vm.log,
            "Rebooting with oVirt agent succeeded",
            "Rebooting with oVirt agent timed out",
        )

    def acpiCallback(self):
        self.vm.log.debug("Rebooting with ACPI")
        if response.is_error(self.vm.acpiReboot()):
            self.vm.log.warning("Rebooting with ACPI FAILED")
            return False

        return utils.log_success(
            self.event.wait(self.timeout),
            self.vm.log,
            "Rebooting with ACPI succeeded",
            "Rebooting with ACPI timed out"
        )

    def forceCallback(self):
        # TODO: fix like acpiShutdown
        self.vm.log.debug("Rebooting with force")
        self.vm._dom.reset(0)

        return utils.log_success(
            self.event.wait(self.timeout),
            self.vm.log,
            "Rebooting with force succeeded",
            "Rebooting with force timed out",
        )
