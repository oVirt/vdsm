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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import threading
import logging

from vdsm.common import exception
from vdsm.common import response
from vdsm.virt import vmpowerdown

from testlib import recorded
from testlib import VdsmTestCase as TestCaseBase
from testValidation import brokentest


class PowerDownTests(TestCaseBase):

    def setUp(self):
        self.dom = FakeDomain()
        self.event = threading.Event()

    # TODO: restore the test once we have a quick way of checking if QEMU GA is
    #       active
    @brokentest("cannot disable QEMU GA callback")
    def test_no_callbacks(self):
        vm = FakeVM(
            self.dom,
            FakeGuestAgent(responsive=False),
            acpiEnable='false'
        )
        obj = make_object('VmPowerDown', vm, self.event)
        res = obj.start()
        assert response.is_error(res, 'exist')

    def test_with_default_callbacks(self):
        vm = FakeVM(
            self.dom,
            FakeGuestAgent(responsive=True),
            acpiEnable='true'
        )
        obj = make_object('VmPowerDown', vm, self.event)
        # no actual callback will be called now!
        res = obj.start()
        assert not response.is_error(res)

    def test_with_forced_callback(self):
        vm = FakeVM(
            self.dom,
            FakeGuestAgent(responsive=True),
            acpiEnable='true'
        )
        obj = make_object('VmPowerDown', vm, self.event, force=True)
        assert obj.forceCallback in \
            [cb.func for cb in obj.chain.callbacks]


class ShutdownTests(TestCaseBase):

    def setUp(self):
        self.dom = FakeDomain()
        self.event = threading.Event()

    def test_qemu_guest_agent_callback_unresponsive(self):
        vm = FakeVM(
            self.dom,
            FakeGuestAgent(responsive=False),
            acpiEnable='true'
        )
        obj = make_object('VmShutdown', vm, self.event)
        assert not obj.qemuGuestAgentCallback()


class RebootTests(TestCaseBase):

    def setUp(self):
        self.dom = FakeDomain()
        self.event = threading.Event()

    def test_qemu_guest_agent_callback_unresponsive(self):
        vm = FakeVM(
            self.dom,
            FakeGuestAgent(responsive=False),
            acpiEnable='true'
        )
        obj = make_object('VmReboot', vm, self.event)
        assert not obj.qemuGuestAgentCallback()


def make_object(name, vm, event, force=False):
    message = 'testing'
    delay = 1.0
    timeout = 1.0
    klass = getattr(vmpowerdown, name)
    return klass(vm, delay, message, timeout, force, event)


class FakeVM(object):

    def __init__(self, dom, ga, acpiEnable='true'):
        self._dom = dom
        self.guestAgent = ga
        self.conf = {'acpiEnable': acpiEnable}
        self.log = logging.getLogger("fake.virt.vm")

    @recorded
    def doDestroy(self):
        pass

    @recorded
    def acpiReboot(self):
        pass

    def qemuGuestAgentShutdown(self):
        if not self.guestAgent.isResponsive():
            raise exception.NonResponsiveGuestAgent()

    def qemuGuestAgentReboot(self):
        if not self.guestAgent.isResponsive():
            raise exception.NonResponsiveGuestAgent()

    def acpi_enabled(self):
        return self.conf['acpiEnable'] == 'true'


class FakeGuestAgent(object):

    def __init__(self, responsive=True):
        self.responsive = responsive

    def isResponsive(self):
        return self.responsive

    @recorded
    def desktopShutdown(self, delay, message, reboot):
        pass


class FakeDomain(object):

    @recorded
    def reset(self, flags=0):
        pass
