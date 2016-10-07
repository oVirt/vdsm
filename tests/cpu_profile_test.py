#
# Copyright 2014-2017 Red Hat, Inc.
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

import errno
import os
import pstats
import time
import threading

from vdsm.profiling import cpu
from vdsm.profiling.errors import UsageError

from monkeypatch import MonkeyPatch
from nose.plugins.skip import SkipTest
from testlib import VdsmTestCase, make_config

yappi = None
try:
    import yappi
except ImportError:
    pass

FILENAME = __file__ + '.prof'


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


def config(enable='true', format='pstat', clock='cpu', builtins='false'):
    return make_config([
        ('devel', 'cpu_profile_enable', enable),
        ('devel', 'cpu_profile_format', format),
        ('devel', 'cpu_profile_clock', clock),
        ('devel', 'cpu_profile_builtins', builtins),
    ])


class ApplicationProfileTests(ProfileTests):

    @MonkeyPatch(cpu, 'config', config())
    @MonkeyPatch(cpu, '_FILENAME', FILENAME)
    def test_pstats_format(self):
        requires_yappi()
        cpu.start()
        cpu.is_running()  # Let if profile something
        cpu.stop()
        self.assertNotRaises(pstats.Stats, FILENAME)

    @MonkeyPatch(cpu, 'config', config(format='ystat'))
    @MonkeyPatch(cpu, '_FILENAME', FILENAME)
    def test_ystats_format(self):
        requires_yappi()
        cpu.start()
        cpu.is_running()  # Let if profile something
        cpu.stop()
        self.assertNotRaises(open_ystats, FILENAME)

    @MonkeyPatch(cpu, 'config', config(format='ystat', builtins='true'))
    @MonkeyPatch(cpu, '_FILENAME', FILENAME)
    def test_with_builtins(self):
        requires_yappi()
        cpu.start()
        dict()
        cpu.stop()
        stats = open_ystats(FILENAME)
        self.assertTrue(find_module(stats, '__builtin__'))

    @MonkeyPatch(cpu, 'config', config(format='ystat', builtins='false'))
    @MonkeyPatch(cpu, '_FILENAME', FILENAME)
    def test_without_builtins(self):
        requires_yappi()
        cpu.start()
        dict()
        cpu.stop()
        stats = open_ystats(FILENAME)
        self.assertFalse(find_module(stats, '__builtin__'))

    @MonkeyPatch(cpu, 'config',
                 config(format='ystat', clock='cpu', builtins='false'))
    @MonkeyPatch(cpu, '_FILENAME', FILENAME)
    def test_cpu_clock(self):
        requires_yappi()
        cpu.start()
        self.sleep(0.1)
        cpu.stop()
        stats = open_ystats(FILENAME)
        name = function_name(self.sleep)
        func = find_function(stats, __file__, name)
        self.assertTrue(func.ttot < 0.1)

    @MonkeyPatch(cpu, 'config',
                 config(format='ystat', clock='wall', builtins='false'))
    @MonkeyPatch(cpu, '_FILENAME', FILENAME)
    def test_wall_clock(self):
        requires_yappi()
        cpu.start()
        self.sleep(0.1)
        cpu.stop()
        stats = open_ystats(FILENAME)
        name = function_name(self.sleep)
        func = find_function(stats, __file__, name)
        self.assertTrue(func.ttot > 0.1)

    @MonkeyPatch(cpu, 'config', config())
    @MonkeyPatch(cpu, '_FILENAME', FILENAME)
    def test_is_running(self):
        requires_yappi()
        self.assertFalse(cpu.is_running())
        cpu.start()
        try:
            self.assertTrue(cpu.is_running())
        finally:
            cpu.stop()
        self.assertFalse(cpu.is_running())

    @MonkeyPatch(cpu, 'config', config(enable='true'))
    def test_is_enabled(self):
        requires_yappi()
        self.assertTrue(cpu.is_enabled())

    # This must succeed even if yappi is not installed
    @MonkeyPatch(cpu, 'config', config(enable='false'))
    def test_disabled(self):
        cpu.start()
        try:
            self.assertFalse(cpu.is_running())
        finally:
            cpu.stop()

    def sleep(self, seconds):
        time.sleep(seconds)


class FunctionProfileTests(ProfileTests):

    # Function profile must succeed if profile is disabled in config.
    @MonkeyPatch(cpu, 'config', config(enable='false'))
    def test_profile_disabled(self):
        requires_yappi()
        self.profiled_function()
        self.assertNotRaises(pstats.Stats, FILENAME)

    # Function profile must fail if profile is enabled in config - we cannot
    # use application wide profile and function profile in the same time.
    @MonkeyPatch(cpu, 'config', config(enable='true'))
    @MonkeyPatch(cpu, '_FILENAME', FILENAME)
    def test_fail_if_Profile_is_running(self):
        requires_yappi()
        cpu.start()
        try:
            self.assertRaises(UsageError,
                              self.profiled_function)
        finally:
            cpu.stop()

    # It is not possible to call a profiled function from a profiled function.
    @MonkeyPatch(cpu, 'config', config(enable='false'))
    def test_fail_recursive_profile(self):
        requires_yappi()
        self.assertRaises(UsageError,
                          self.recursive_profile)

    @MonkeyPatch(cpu, 'config', config(enable='false'))
    def test_ystat_format(self):
        requires_yappi()
        self.ystat_format()
        self.assertNotRaises(open_ystats, FILENAME)

    @MonkeyPatch(cpu, 'config', config(enable='false'))
    def test_with_builtins(self):
        requires_yappi()
        self.with_builtins()
        stats = open_ystats(FILENAME)
        self.assertTrue(find_module(stats, '__builtin__'))

    @MonkeyPatch(cpu, 'config', config(enable='false'))
    def test_without_builtins(self):
        requires_yappi()
        self.without_builtins()
        stats = open_ystats(FILENAME)
        self.assertFalse(find_module(stats, '__builtin__'))

    @MonkeyPatch(cpu, 'config', config(enable='false'))
    def test_cpu_clock(self):
        requires_yappi()
        self.cpu_clock()
        stats = open_ystats(FILENAME)
        name = function_name(self.cpu_clock)
        func = find_function(stats, __file__, name)
        self.assertTrue(func.ttot < 0.1)

    @MonkeyPatch(cpu, 'config', config(enable='false'))
    def test_wall_clock(self):
        requires_yappi()
        self.wall_clock()
        stats = open_ystats(FILENAME)
        name = function_name(self.wall_clock)
        func = find_function(stats, __file__, name)
        self.assertTrue(func.ttot > 0.1)

    @cpu.profile(FILENAME)
    def profiled_function(self):
        self.assertTrue(cpu.is_running())

    @cpu.profile(FILENAME)
    def recursive_profile(self):
        self.profiled_function()

    @cpu.profile(FILENAME, format="ystat")
    def ystat_format(self):
        pass

    @cpu.profile(FILENAME, format="ystat", builtins=False)
    def without_builtins(self):
        pass

    @cpu.profile(FILENAME, format="ystat", builtins=True)
    def with_builtins(self):
        pass

    @cpu.profile(FILENAME, format="ystat", clock="cpu")
    def cpu_clock(self):
        time.sleep(0.1)

    @cpu.profile(FILENAME, format="ystat", clock="wall")
    def wall_clock(self):
        time.sleep(0.1)


class ThreadsProfileTests(ProfileTests):

    def setUp(self):
        self.thread = None
        self.ready = threading.Event()
        self.resume = threading.Event()

    @MonkeyPatch(cpu, 'config', config(enable='false'))
    def test_new_threads(self):
        # The easy case - threads started after yappi was started
        requires_yappi()
        self.new_threads()
        stats = open_ystats(FILENAME)
        name = function_name(self.worker_function)
        func = find_function(stats, __file__, name)
        self.assertEqual(func.ncall, 1)

    @MonkeyPatch(cpu, 'config', config(enable='false'))
    def test_running_threads(self):
        # The harder case - threads started before yappi was started
        requires_yappi()
        self.start_thread()
        self.running_threads()
        stats = open_ystats(FILENAME)
        name = function_name(self.worker_function)
        func = find_function(stats, __file__, name)
        self.assertEqual(func.ncall, 1)

    @MonkeyPatch(cpu, 'config', config(enable='false'))
    def test_without_threads(self):
        requires_yappi()
        self.without_threads()
        stats = open_ystats(FILENAME)
        name = function_name(self.worker_function)
        self.assertRaises(NotFound, find_function, stats, __file__, name)

    @cpu.profile(FILENAME, format="ystat", threads=True)
    def new_threads(self):
        self.start_thread()
        self.join_thread()

    @cpu.profile(FILENAME, format="ystat", threads=True)
    def running_threads(self):
        self.join_thread()

    @cpu.profile(FILENAME, format="ystat", threads=False)
    def without_threads(self):
        self.start_thread()
        self.join_thread()

    def start_thread(self):
        self.thread = threading.Thread(target=self.worker)
        self.thread.daemon = True
        self.thread.start()
        self.ready.wait()

    def join_thread(self):
        self.resume.set()
        self.thread.join()

    def worker(self):
        self.ready.set()
        self.resume.wait()
        self.worker_function()

    def worker_function(self):
        pass


# Helpers

def open_ystats(filename):
    stats = yappi.YFuncStats()
    stats.add(filename)
    return stats


def find_module(ystats, name):
    return any(func.module == name for func in ystats)


class NotFound(Exception):
    pass


def find_function(ystats, module, name):
    for func in ystats:
        if func.module == module and func.name == name:
            return func
    raise NotFound('No such function: %s(%s)' % (module, name))


def function_name(meth):
    return meth.__self__.__class__.__name__ + '.' + meth.__name__
