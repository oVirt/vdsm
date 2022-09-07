# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import errno
import json
import os
import subprocess
import threading
from functools import wraps

from nose.plugins.skip import SkipTest
from nose.plugins import Plugin

from vdsm import utils

import six


class SlowTestsPlugin(Plugin):
    """
    Tests that might be too slow to run on every build are marked with the
    @slowtest plugin, and disable by default. Use this plugin to enable these
    tests.
    """
    name = 'slowtests'
    enabled = False

    def add_options(self, parser, env=os.environ):
        env_opt = 'NOSE_SLOW_TESTS'
        if env is None:
            default = False
        else:
            default = env.get(env_opt)

        parser.add_option('--enable-slow-tests',
                          action='store_true',
                          default=default,
                          dest='enable_slow_tests',
                          help='Some tests might take a long time to run, ' +
                               'use this to enable slow tests.' +
                               '  [%s]' % env_opt)

    def configure(self, options, conf):
        Plugin.configure(self, options, conf)
        if options.enable_slow_tests:
            SlowTestsPlugin.enabled = True


class StressTestsPlugin(Plugin):
    """
    Tests that stress the resources of the machine, are too slow to run on each
    build and may fail on overloaded machines or machines with unpreditable
    resources.

    These tests are mark with @stresstest decorator and are disabled by
    default. Use this plugin to enable these tests.
    """
    name = 'stresstests'
    enabled = False

    def add_options(self, parser, env=os.environ):
        env_opt = 'NOSE_STRESS_TESTS'
        if env is None:
            default = False
        else:
            default = env.get(env_opt)

        parser.add_option('--enable-stress-tests',
                          action='store_true',
                          default=default,
                          dest='enable_stress_tests',
                          help='Some tests stress the resources of the ' +
                               'system running the tests. Use this to ' +
                               'enable stress tests [%s]' % env_opt)

    def configure(self, options, conf):
        Plugin.configure(self, options, conf)
        if options.enable_stress_tests:
            StressTestsPlugin.enabled = True


class ThreadLeakPlugin(Plugin):
    """
    Check whether a test (or the code it triggers) leaks threads
    """
    name = 'thread-leak-check'

    def _threads(self):
        return frozenset(threading.enumerate())

    def startTest(self, test):
        self._start_threads = self._threads()

    def stopTest(self, test):
        leaked_threads = self._threads() - self._start_threads
        if leaked_threads:
            raise Exception('This test leaked threads: %s ' % leaked_threads)


class ProcessLeakPlugin(Plugin):
    """
    Fail tests that leaked child processes.

    Tests starting child process must wait for the child process before
    returning from the test. Not waiting may casue the test or the next test to
    fail when the child process exit and the test framework received unexpected
    SIGCHLD.

    Running the tests with --with-process-leak-check will fail any test that
    leaked a child process.
    """
    PGREP_CMD = ("pgrep", "-P", "%s" % os.getpid())
    name = 'process-leak-check'

    def startTest(self, test):
        self._start_processes = self._child_processes()

    def stopTest(self, test):
        leaked_processes = self._child_processes() - self._start_processes
        if leaked_processes:
            info = [dict(pid=pid, cmdline=utils.getCmdArgs(pid))
                    for pid in leaked_processes]
            raise AssertionError("Test leaked child processes:\n" +
                                 json.dumps(info, indent=4))

    def _child_processes(self):
        proc = subprocess.Popen(
            self.PGREP_CMD, stdin=None, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        out, err = proc.communicate()
        # EXIT STATUS
        # 0      One or more processes matched the criteria.
        # 1      No processes matched.
        if proc.returncode not in (0, 1):
            raise RuntimeError("Error running pgrep: [%d] %s"
                               % (proc.returncode, err))
        return frozenset(int(pid) for pid in out.splitlines())


class FileLeakPlugin(Plugin):
    """
    Check whether a test (or the code it triggers) open files and do not close
    them.
    """
    name = 'file-leak-check'
    FD_DIR = '/proc/%s/fd' % os.getpid()

    def _fd_desc(self, fd):
        try:
            return os.readlink(os.path.join(self.FD_DIR, fd))
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
            return 'fd:%s' % fd

    def _open_files(self):
        return frozenset(self._fd_desc(fd) for fd in os.listdir(self.FD_DIR))

    def startTest(self, test):
        self._start_files = self._open_files()

    def stopTest(self, test):
        leaked_files = self._open_files() - self._start_files
        if leaked_files:
            raise Exception('This test leaked files: %s' % leaked_files)


def ValidateRunningAsRoot(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if os.geteuid() != 0:
            raise SkipTest("This test must be run as root")

        return f(*args, **kwargs)

    return wrapper


def ValidateNotRunningAsRoot(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if os.geteuid() == 0:
            raise SkipTest("This test must not run as root")

        return f(*args, **kwargs)

    return wrapper


def slowtest(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not SlowTestsPlugin.enabled:
            raise SkipTest("Slow tests are disabled")

        return f(*args, **kwargs)

    return wrapper


def xfail(reason):
    """
    Mark a test as expected failure.

    This decorator should be used to mark good tests as expected failure. In
    this case the test is good, but the code is broken, and cannot be fix yet.

    The test will skip with the reason message if the test fail, and fail if
    the test succeeds, since this means the code is working and we can remove
    this decorator.

    This is a poor man implementation of pytest.mark.xfail, see
    http://doc.pytest.org/en/latest/skipping.html

    Usage::

        @xfail("why this test canonot pass now...")
        def test_broken_code(self):
            ...
    """
    _check_decorator_misuse(reason)

    def wrap(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            try:
                f(*args, **kwargs)
            except:
                raise SkipTest(reason)
            else:
                raise AssertionError("This test is expected to fail")
        return wrapper

    return wrap


def skipif(cond, reason):
    """
    Skip a test if cond is True.

    Usage::

        @skipif(six.PY3, "Needs porting to python 3")
        def test_rusty_pyton_2_code(self):
            ...
    """
    _check_decorator_misuse(reason)

    def wrap(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if cond:
                raise SkipTest(reason)
            return f(*args, **kwargs)
        return wrapper

    return wrap


def brokentest(reason):
    """
    Mark a test as broken.

    Usage::

        @brokentest("why it is broken...")
        def test_will_skip_on_failure(self):
            ...
    """
    _check_decorator_misuse(reason)

    def wrap(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except:
                raise SkipTest(reason)
        return wrapper

    return wrap


def broken_on_ci(reason, exception=Exception, name="OVIRT_CI"):
    """
    Mark a test as broken on the CI.

    By defualt, this will skip failing tests run in ovirt CI, when OVIRT_CI
    environment variable is defined.

    To use on travis-ci, use name="TRAVIS_CI".  If a test is broken on both
    ovirt CI and travis-ci, mark it separately for each.

    Usage::

        @broken_on_ci("why it is broken...")
        def test_will_skip_on_failure(self):
            ...

    To skip only if certain expection was raised, you can specify the
    expection::

        @broken_on_ci("why it is broken...", exception=OSError)
        def test_will_skip_on_os_error(self):
            ...
    """
    _check_decorator_misuse(reason)

    def wrap(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except exception:
                if os.environ.get(name):
                    raise SkipTest(reason)
                else:
                    raise
        return wrapper

    return wrap


def stresstest(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not StressTestsPlugin.enabled:
            raise SkipTest("Stress tests are disabled")

        return f(*args, **kwargs)

    return wrapper


def checkSudo(cmd):
    try:
        p = subprocess.Popen(['sudo', '-l', '-n'] + cmd,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    except OSError as e:
        if e.errno == errno.ENOENT:
            raise SkipTest("Test requires SUDO executable (%s)" % e)
        else:
            raise

    out, err = p.communicate()

    if p.returncode != 0:
        raise SkipTest("Test requires SUDO configuration (%s)" % err.strip())


def _check_decorator_misuse(arg):
    """
    Validate decorator correct usage by checking the decorator first
    argument type.
    The decorators checked by this function are expected to be used as a
    function where their first argument type is a string.
    Decorators that are not used as a function call, have their single argument
    as the method they wrap, which is not of type string.
    """
    if not isinstance(arg, six.string_types):
        raise TypeError("First argument should be a string. "
                        "Has the decorator been used as a function call?")
