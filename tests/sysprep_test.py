#
# Copyright 2018 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

from vdsm import virtsysprep
from vdsm.common import cmdutils
from vdsm.virt.utils import LibguestfsCommand

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase as TestCaseBase


BLANK_UUID = '00000000-0000-0000-0000-000000000000'
FAKE_VOLUME = '/we/dont/care/about/this/path'


FakeCommand = LibguestfsCommand('/bin/false')


class VirtSysprepTests(TestCaseBase):

    @MonkeyPatch(virtsysprep, '_VIRTSYSPREP', FakeCommand)
    def test_raise_error_on_failure(self):

        self.assertRaises(cmdutils.Error,
                          virtsysprep.sysprep,
                          BLANK_UUID, [FAKE_VOLUME])
