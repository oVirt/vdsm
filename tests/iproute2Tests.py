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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from subprocess import Popen, PIPE

from testlib import VdsmTestCase as TestCaseBase
from testValidation import ValidateRunningAsRoot

from vdsm.constants import EXT_BRCTL
from network.configurators.iproute2 import _BRCTL_DEV_EXISTS

from tcTests import _Bridge, _checkDependencies


class TestBridgeOverwrite(TestCaseBase):
    _bridge = _Bridge()

    @ValidateRunningAsRoot
    def setUp(self):
        _checkDependencies()
        self._bridge.addDevice()

    def tearDown(self):
        self._bridge.delDevice()

    def testBridgeOverwriteErr(self):
        """iproute2 configurator uses hardcoded error output, test it's still
        the same"""
        expected_err = _BRCTL_DEV_EXISTS % self._bridge.devName
        popen = Popen([EXT_BRCTL, 'addbr', self._bridge.devName], stderr=PIPE)
        err = popen.stderr.read().strip()
        self.assertEquals(expected_err, err)
