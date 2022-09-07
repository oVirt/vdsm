# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import pstats
import time
import threading

from contextlib import contextmanager

from vdsm.profiling import cpu
from vdsm.profiling.errors import UsageError

from monkeypatch import MonkeyPatchScope
from nose.plugins.skip import SkipTest
from testlib import VdsmTestCase, make_config
from testlib import temporaryPath

yappi = None
try:
    import yappi
except ImportError:
    pass


def requires_yappi():
    if yappi is None:
        raise SkipTest('yappi is not installed')


@contextmanager
def env(enable='true', format='pstat', clock='cpu', builtins='false'):
    with temporaryPath() as filename:
        config = make_config([
            ('devel', 'cpu_profile_enable', enable),
            ('devel', 'cpu_profile_filename', filename),
            ('devel', 'cpu_profile_format', format),
            ('devel', 'cpu_profile_clock', clock),
            ('devel', 'cpu_profile_builtins', builtins),
        ])
        with MonkeyPatchScope([(cpu, 'config', config)]):
            yield filename


class ApplicationProfileTests(VdsmTestCase):

    def test_pstats_format(self):
        requires_yappi()
        with env() as filename:
            cpu.start()
            cpu.is_running()  # Let if profile something
            cpu.stop()
            self.assertNotRaises(pstats.Stats, filename)

    def test_ystats_format(self):
        requires_yappi()
        with env(format='ystat') as filename:
            cpu.start()
            cpu.is_running()  # Let if profile something
            cpu.stop()
            self.assertNotRaises(open_ystats, filename)

    def test_with_builtins(self):
        requires_yappi()
        with env(format='ystat', builtins='true') as filename:
            cpu.start()
            dict()
            cpu.stop()
            stats = open_ystats(filename)
            self.assertTrue(find_module(stats, '__builtin__'))

    def test_without_builtins(self):
        requires_yappi()
        with env(format='ystat', builtins='false') as filename:
            cpu.start()
            dict()
            cpu.stop()
            stats = open_ystats(filename)
            self.assertFalse(find_module(stats, '__builtin__'))

    def test_cpu_clock(self):
        requires_yappi()
        with env(format='ystat', clock='cpu', builtins='false') as filename:
            cpu.start()
            self.sleep(0.1)
            cpu.stop()
            stats = open_ystats(filename)
            name = function_name(self.sleep)
            func = find_function(stats, __file__, name)
            self.assertTrue(func.ttot < 0.1)

    def test_wall_clock(self):
        requires_yappi()
        with env(format='ystat', clock='wall', builtins='false') as filename:
            cpu.start()
            self.sleep(0.1)
            cpu.stop()
            stats = open_ystats(filename)
            name = function_name(self.sleep)
            func = find_function(stats, __file__, name)
            self.assertTrue(func.ttot > 0.1)

    def test_is_running(self):
        requires_yappi()
        with env():
            self.assertFalse(cpu.is_running())
            cpu.start()
            try:
                self.assertTrue(cpu.is_running())
            finally:
                cpu.stop()
            self.assertFalse(cpu.is_running())

    def test_is_enabled(self):
        requires_yappi()
        with env(enable='true'):
            self.assertTrue(cpu.is_enabled())

    # This must succeed even if yappi is not installed
    def test_disabled(self):
        with env(enable='false'):
            cpu.start()
            try:
                self.assertFalse(cpu.is_running())
            finally:
                cpu.stop()

    def sleep(self, seconds):
        time.sleep(seconds)


class FunctionProfileTests(VdsmTestCase):

    # Function profile must succeed if profile is disabled in config.
    def test_profile_disabled(self):
        requires_yappi()
        with env(enable='false') as filename:

            @cpu.profile(filename)
            def profiled_function():
                self.assertTrue(cpu.is_running())

            profiled_function()
            self.assertNotRaises(pstats.Stats, filename)

    # Function profile must fail if profile is enabled in config - we cannot
    # use application wide profile and function profile in the same time.
    def test_fail_if_Profile_is_running(self):
        requires_yappi()
        with env(enable='true') as filename:

            @cpu.profile(filename)
            def profiled_function():
                self.assertTrue(cpu.is_running())

            cpu.start()
            try:
                self.assertRaises(UsageError, profiled_function)
            finally:
                cpu.stop()

    # It is not possible to call a profiled function from a profiled function.
    def test_fail_recursive_profile(self):
        requires_yappi()
        with env(enable='false') as filename:

            @cpu.profile(filename)
            def recursive_profile():
                profiled_function()

            @cpu.profile(filename)
            def profiled_function():
                self.assertTrue(cpu.is_running())

            self.assertRaises(UsageError, recursive_profile)

    def test_ystat_format(self):
        requires_yappi()
        with env(enable='false') as filename:

            @cpu.profile(filename, format="ystat")
            def ystat_format():
                pass

            ystat_format()
            self.assertNotRaises(open_ystats, filename)

    def test_with_builtins(self):
        requires_yappi()
        with env(enable='false') as filename:

            @cpu.profile(filename, format="ystat", builtins=True)
            def with_builtins():
                pass

            with_builtins()
            stats = open_ystats(filename)
            self.assertTrue(find_module(stats, '__builtin__'))

    def test_without_builtins(self):
        requires_yappi()
        with env(enable='false') as filename:

            @cpu.profile(filename, format="ystat", builtins=False)
            def without_builtins():
                pass

            without_builtins()
            stats = open_ystats(filename)
            self.assertFalse(find_module(stats, '__builtin__'))

    def test_cpu_clock(self):
        requires_yappi()
        with env(enable='false') as filename:

            @cpu.profile(filename, format="ystat", clock="cpu")
            def cpu_clock():
                time.sleep(0.1)

            cpu_clock()
            stats = open_ystats(filename)
            func = find_function(stats, __file__, "cpu_clock")
            self.assertTrue(func.ttot < 0.1)

    def test_wall_clock(self):
        requires_yappi()
        with env(enable='false') as filename:

            @cpu.profile(filename, format="ystat", clock="wall")
            def wall_clock():
                time.sleep(0.1)

            wall_clock()
            stats = open_ystats(filename)
            func = find_function(stats, __file__, "wall_clock")
            self.assertTrue(func.ttot > 0.1)


class ThreadsProfileTests(VdsmTestCase):

    def setUp(self):
        self.thread = None
        self.ready = threading.Event()
        self.resume = threading.Event()

    def test_new_threads(self):
        # The easy case - threads started after yappi was started
        requires_yappi()
        with env(enable='false') as filename:

            @cpu.profile(filename, format="ystat", threads=True)
            def new_threads():
                self.start_thread()
                self.join_thread()

            new_threads()
            stats = open_ystats(filename)
            name = function_name(self.worker_function)
            func = find_function(stats, __file__, name)
            self.assertEqual(func.ncall, 1)

    def test_running_threads(self):
        # The harder case - threads started before yappi was started
        requires_yappi()
        with env(enable='false') as filename:

            @cpu.profile(filename, format="ystat", threads=True)
            def running_threads():
                self.join_thread()

            self.start_thread()
            running_threads()
            stats = open_ystats(filename)
            name = function_name(self.worker_function)
            func = find_function(stats, __file__, name)
            self.assertEqual(func.ncall, 1)

    def test_without_threads(self):
        requires_yappi()
        with env(enable='false') as filename:

            @cpu.profile(filename, format="ystat", threads=False)
            def without_threads():
                self.start_thread()
                self.join_thread()

            without_threads()
            stats = open_ystats(filename)
            name = function_name(self.worker_function)
            self.assertRaises(NotFound, find_function, stats, __file__, name)

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
