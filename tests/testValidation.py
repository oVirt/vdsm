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

import pytest

from vdsm import utils


class SlowTestsPlugin:
    """
    Tests that might be too slow to run on every build are marked with the
    @slowtest decorator, and disable by default. Use this plugin to enable these
    tests.
    """
    name = 'slowtests'
    enabled = False

    def pytest_addoption(self, parser):
        parser.addoption(
            '--enable-slow-tests',
            action='store_true',
            default=False,
            help='Some tests might take a long time to run, ' +
                 'use this to enable slow tests.'
        )

    def pytest_configure(self, config):
        if (config.getoption('--enable-slow-tests') or 
            os.environ.get('PYTEST_SLOW_TESTS')):
            SlowTestsPlugin.enabled = True


class StressTestsPlugin:
    """
    Tests that stress the resources of the machine, are too slow to run on each
    build and may fail on overloaded machines or machines with unpredictable
    resources.

    These tests are marked with @stresstest decorator and are disabled by
    default. Use this plugin to enable these tests.
    """
    name = 'stresstests'
    enabled = False

    def pytest_addoption(self, parser):
        parser.addoption(
            '--enable-stress-tests',
            action='store_true',
            default=False,
            help='Some tests stress the resources of the ' +
                 'system running the tests. Use this to ' +
                 'enable stress tests'
        )

    def pytest_configure(self, config):
        if (config.getoption('--enable-stress-tests') or
            os.environ.get('PYTEST_STRESS_TESTS')):
            StressTestsPlugin.enabled = True


class ThreadLeakPlugin:
    """
    Check whether a test (or the code it triggers) leaks threads
    """
    name = 'thread-leak-check'
    enabled = False

    def pytest_addoption(self, parser):
        parser.addoption(
            '--enable-thread-leak-check',
            action='store_true',
            default=False,
            help='Enable thread leak detection for tests'
        )

    def pytest_configure(self, config):
        if (config.getoption('--enable-thread-leak-check') or
            os.environ.get('PYTEST_THREAD_LEAK_CHECK')):
            ThreadLeakPlugin.enabled = True

    def _threads(self):
        return frozenset(threading.enumerate())

    def pytest_runtest_setup(self, item):
        if not self.enabled:
            return
        self._start_threads = self._threads()

    def pytest_runtest_teardown(self, item, nextitem):
        if not self.enabled:
            return
        leaked_threads = self._threads() - self._start_threads
        if leaked_threads:
            raise Exception('This test leaked threads: %s ' % leaked_threads)


class ProcessLeakPlugin:
    """
    Fail tests that leaked child processes.

    Tests starting child process must wait for the child process before
    returning from the test. Not waiting may casue the test or the next test to
    fail when the child process exit and the test framework received unexpected
    SIGCHLD.

    Running the tests with --with-process-leak-check will fail any test that
    leaked a child process.
    """
    name = 'process-leak-check'
    enabled = False
    PGREP_CMD = ("pgrep", "-P", "%s" % os.getpid())

    def pytest_addoption(self, parser):
        parser.addoption(
            '--enable-process-leak-check',
            action='store_true',
            default=False,
            help='Enable process leak detection for tests'
        )

    def pytest_configure(self, config):
        if (config.getoption('--enable-process-leak-check') or
            os.environ.get('PYTEST_PROCESS_LEAK_CHECK')):
            ProcessLeakPlugin.enabled = True

    def pytest_runtest_setup(self, item):
        if not self.enabled:
            return
        self._start_processes = self._child_processes()

    def pytest_runtest_teardown(self, item, nextitem):
        if not self.enabled:
            return
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


class FileLeakPlugin:
    """
    Check whether a test (or the code it triggers) open files and do not close
    them.
    """
    name = 'file-leak-check'
    enabled = False
    FD_DIR = '/proc/%s/fd' % os.getpid()

    def pytest_addoption(self, parser):
        parser.addoption(
            '--enable-file-leak-check',
            action='store_true',
            default=False,
            help='Enable file descriptor leak detection for tests'
        )

    def pytest_configure(self, config):
        if (config.getoption('--enable-file-leak-check') or
            os.environ.get('PYTEST_FILE_LEAK_CHECK')):
            FileLeakPlugin.enabled = True

    def _fd_desc(self, fd):
        try:
            link_target = os.readlink(os.path.join(self.FD_DIR, fd))
            # Try to get more info about the file descriptor
            try:
                fd_info = os.stat(os.path.join(self.FD_DIR, fd))
                return f'{link_target} (fd:{fd}, mode:{oct(fd_info.st_mode)})'
            except (OSError, ValueError):
                return f'{link_target} (fd:{fd})'
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
            return 'fd:%s' % fd

    def _open_files(self):
        return frozenset(self._fd_desc(fd) for fd in os.listdir(self.FD_DIR))

    def _detailed_fd_info(self):
        """Get detailed information about current file descriptors"""
        info = {}
        try:
            for fd in os.listdir(self.FD_DIR):
                try:
                    link = os.readlink(os.path.join(self.FD_DIR, fd))
                    info[fd] = link
                except OSError:
                    info[fd] = f"fd:{fd} (unable to read link)"
        except OSError:
            pass
        return info

    def pytest_runtest_setup(self, item):
        if not self.enabled:
            return
        self._start_files = self._open_files()

    def pytest_runtest_teardown(self, item, nextitem):
        if not self.enabled:
            return
            
        leaked_files = self._open_files() - self._start_files
        if leaked_files:
            # Print detailed debugging information
            print(f"\n=== FILE LEAK DETECTED in {item.name} ===")
            print(f"Leaked files: {leaked_files}")
            
            # Show current fd state for debugging
            current_fds = self._detailed_fd_info()
            print(f"Current file descriptors:")
            for fd, desc in sorted(current_fds.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999):
                print(f"  fd {fd}: {desc}")
            
            raise Exception('This test leaked files: %s' % leaked_files)


def ValidateRunningAsRoot(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if os.geteuid() != 0:
            pytest.skip("This test must be run as root")

        return f(*args, **kwargs)

    return wrapper


def ValidateNotRunningAsRoot(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if os.geteuid() == 0:
            pytest.skip("This test must not run as root")

        return f(*args, **kwargs)

    return wrapper


def slowtest(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not SlowTestsPlugin.enabled:
            pytest.skip("Slow tests are disabled")

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
                pytest.skip(reason)
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
                pytest.skip(reason)
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
                pytest.skip(reason)
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
                    pytest.skip(reason)
                else:
                    raise
        return wrapper

    return wrap


def stresstest(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not StressTestsPlugin.enabled:
            pytest.skip("Stress tests are disabled")

        return f(*args, **kwargs)

    return wrapper


def checkSudo(cmd):
    try:
        p = subprocess.Popen(['sudo', '-l', '-n'] + cmd,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    except OSError as e:
        if e.errno == errno.ENOENT:
            pytest.skip("Test requires SUDO executable (%s)" % e)
        else:
            raise

    out, err = p.communicate()

    if p.returncode != 0:
        pytest.skip("Test requires SUDO configuration (%s)" % err.strip())


def _check_decorator_misuse(arg):
    """
    Validate decorator correct usage by checking the decorator first
    argument type.
    The decorators checked by this function are expected to be used as a
    function where their first argument type is a string.
    Decorators that are not used as a function call, have their single argument
    as the method they wrap, which is not of type string.
    """
    if not isinstance(arg, str):
        raise TypeError("First argument should be a string. "
                        "Has the decorator been used as a function call?")
