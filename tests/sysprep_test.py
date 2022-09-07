# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
