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
import time

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


class ProfileTests(VdsmTestCase):

    def tearDown(self):
        try:
            os.unlink(FILENAME)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise


class ApplicationProfileTests(ProfileTests):

    @MonkeyPatch(profile, 'config', make_config(enable='true'))
    @MonkeyPatch(profile, '_FILENAME', FILENAME)
    @MonkeyPatch(profile, '_FORMAT', 'pstat')
    def test_pstats_format(self):
        requires_yappi()
        profile.start()
        profile.is_running()  # Let if profile something
        profile.stop()
        self.assertNotRaises(pstats.Stats, FILENAME)

    @MonkeyPatch(profile, 'config', make_config(enable='true'))
    @MonkeyPatch(profile, '_FILENAME', FILENAME)
    @MonkeyPatch(profile, '_FORMAT', 'ystat')
    def test_ystats_format(self):
        requires_yappi()
        profile.start()
        profile.is_running()  # Let if profile something
        profile.stop()
        self.assertNotRaises(open_ystats, FILENAME)

    @MonkeyPatch(profile, 'config', make_config(enable='true'))
    @MonkeyPatch(profile, '_FILENAME', FILENAME)
    @MonkeyPatch(profile, '_FORMAT', 'ystat')
    @MonkeyPatch(profile, '_BUILTINS', True)
    def test_with_builtins(self):
        requires_yappi()
        profile.start()
        dict()
        profile.stop()
        stats = open_ystats(FILENAME)
        self.assertTrue(find_module(stats, '__builtin__'))

    @MonkeyPatch(profile, 'config', make_config(enable='true'))
    @MonkeyPatch(profile, '_FILENAME', FILENAME)
    @MonkeyPatch(profile, '_FORMAT', 'ystat')
    @MonkeyPatch(profile, '_BUILTINS', False)
    def test_without_builtins(self):
        requires_yappi()
        profile.start()
        dict()
        profile.stop()
        stats = open_ystats(FILENAME)
        self.assertFalse(find_module(stats, '__builtin__'))

    @MonkeyPatch(profile, 'config', make_config(enable='true'))
    @MonkeyPatch(profile, '_FILENAME', FILENAME)
    @MonkeyPatch(profile, '_FORMAT', 'ystat')
    @MonkeyPatch(profile, '_CLOCK', 'cpu')
    def test_cpu_clock(self):
        requires_yappi()
        profile.start()
        self.sleep(0.1)
        profile.stop()
        stats = open_ystats(FILENAME)
        name = function_name(self.sleep)
        func = find_function(stats, __file__, name)
        self.assertTrue(func.ttot < 0.1)

    @MonkeyPatch(profile, 'config', make_config(enable='true'))
    @MonkeyPatch(profile, '_FILENAME', FILENAME)
    @MonkeyPatch(profile, '_FORMAT', 'ystat')
    @MonkeyPatch(profile, '_CLOCK', 'wall')
    def test_wall_clock(self):
        requires_yappi()
        profile.start()
        self.sleep(0.1)
        profile.stop()
        stats = open_ystats(FILENAME)
        name = function_name(self.sleep)
        func = find_function(stats, __file__, name)
        self.assertTrue(func.ttot > 0.1)

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

    def sleep(self, seconds):
        time.sleep(seconds)


class FunctionProfileTests(ProfileTests):

    # Function profile must succeed if profile is disabled in config.
    @MonkeyPatch(profile, 'config', make_config(enable='false'))
    def test_profile_disabled(self):
        requires_yappi()
        self.profiled_function()
        self.assertNotRaises(pstats.Stats, FILENAME)

    # Function profile must fail if profile is enabled in config - we cannot
    # use application wide profile and function profile in the same time.
    @MonkeyPatch(profile, 'config', make_config(enable='true'))
    @MonkeyPatch(profile, '_FILENAME', FILENAME)
    def test_fail_if_Profile_is_running(self):
        requires_yappi()
        profile.start()
        try:
            self.assertRaises(profile.Error, self.profiled_function)
        finally:
            profile.stop()

    # It is not possible to call a profiled function from a profiled function.
    @MonkeyPatch(profile, 'config', make_config(enable='false'))
    def test_fail_recursive_profile(self):
        requires_yappi()
        self.assertRaises(profile.Error, self.recursive_profile)

    @MonkeyPatch(profile, 'config', make_config(enable='false'))
    def test_ystat_format(self):
        requires_yappi()
        self.ystat_format()
        self.assertNotRaises(open_ystats, FILENAME)

    @MonkeyPatch(profile, 'config', make_config(enable='false'))
    def test_with_builtins(self):
        requires_yappi()
        self.with_builtins()
        stats = open_ystats(FILENAME)
        self.assertTrue(find_module(stats, '__builtin__'))

    @MonkeyPatch(profile, 'config', make_config(enable='false'))
    def test_without_builtins(self):
        requires_yappi()
        self.without_builtins()
        stats = open_ystats(FILENAME)
        self.assertFalse(find_module(stats, '__builtin__'))

    @MonkeyPatch(profile, 'config', make_config(enable='false'))
    def test_cpu_clock(self):
        requires_yappi()
        self.cpu_clock()
        stats = open_ystats(FILENAME)
        name = function_name(self.cpu_clock)
        func = find_function(stats, __file__, name)
        self.assertTrue(func.ttot < 0.1)

    @MonkeyPatch(profile, 'config', make_config(enable='false'))
    def test_wall_clock(self):
        requires_yappi()
        self.wall_clock()
        stats = open_ystats(FILENAME)
        name = function_name(self.wall_clock)
        func = find_function(stats, __file__, name)
        self.assertTrue(func.ttot > 0.1)

    @profile.profile(FILENAME)
    def profiled_function(self):
        self.assertTrue(profile.is_running())

    @profile.profile(FILENAME)
    def recursive_profile(self):
        self.profiled_function()

    @profile.profile(FILENAME, format="ystat")
    def ystat_format(self):
        pass

    @profile.profile(FILENAME, format="ystat", builtins=False)
    def without_builtins(self):
        pass

    @profile.profile(FILENAME, format="ystat", builtins=True)
    def with_builtins(self):
        pass

    @profile.profile(FILENAME, format="ystat", clock="cpu")
    def cpu_clock(self):
        time.sleep(0.1)

    @profile.profile(FILENAME, format="ystat", clock="wall")
    def wall_clock(self):
        time.sleep(0.1)

# Helpers

def open_ystats(filename):
    stats = yappi.YFuncStats()
    stats.add(filename)
    return stats


def find_module(ystats, name):
    return any(func.module == name for func in ystats)


def find_function(ystats, module, name):
    for func in ystats:
        if func.module == module and func.name == name:
            return func
    raise Exception('No such function: %s(%s)' % (module, name))


def function_name(meth):
    return meth.im_class.__name__ + '.' + meth.__name__
