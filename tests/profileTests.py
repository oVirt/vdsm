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

import ConfigParser
import errno
import os
import pstats

from vdsm import profile
from vdsm import config

from monkeypatch import MonkeyPatch
from nose.plugins.skip import SkipTest
from testrunner import VdsmTestCase

yappi = None
try:
    import yappi
except ImportError:
    pass

FILENAME = __file__ + '.prof'


def make_config(enable='false'):
    cfg = ConfigParser.ConfigParser()
    config.set_defaults(cfg)
    cfg.set('vars', 'profile_enable', enable)
    return cfg


def requires_yappi():
    if yappi is None:
        raise SkipTest('yappi is not installed')


class ApplicationProfileTests(VdsmTestCase):

    def tearDown(self):
        try:
            os.unlink(FILENAME)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise

    @MonkeyPatch(profile, 'config', make_config(enable='true'))
    @MonkeyPatch(profile, '_FILENAME', FILENAME)
    @MonkeyPatch(profile, '_FORMAT', 'pstat')
    def test_pstats_format(self):
        requires_yappi()
        profile.start()
        profile.is_running()  # Let if profile something
        profile.stop()
        pstats.Stats(FILENAME)

    @MonkeyPatch(profile, 'config', make_config(enable='true'))
    @MonkeyPatch(profile, '_FILENAME', FILENAME)
    @MonkeyPatch(profile, '_FORMAT', 'ystat')
    def test_ystats_format(self):
        requires_yappi()
        profile.start()
        profile.is_running()  # Let if profile something
        profile.stop()
        stats = yappi.YFuncStats()
        stats.add(FILENAME)

    @MonkeyPatch(profile, 'config', make_config(enable='true'))
    @MonkeyPatch(profile, '_FILENAME', FILENAME)
    def test_is_running(self):
        requires_yappi()
        self.assertFalse(profile.is_running())
        profile.start()
        try:
            self.assertTrue(profile.is_running())
        finally:
            profile.stop()
        self.assertFalse(profile.is_running())

    @MonkeyPatch(profile, 'config', make_config(enable='true'))
    def test_is_enabled(self):
        requires_yappi()
        self.assertTrue(profile.is_enabled())

    # This must succeed even if yappi is not installed
    @MonkeyPatch(profile, 'config', make_config(enable='false'))
    def test_disabled(self):
        profile.start()
        try:
            self.assertFalse(profile.is_running())
        finally:
            profile.stop()
