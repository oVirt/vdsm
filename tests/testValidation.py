#
# Copyright 2009-2014 Red Hat, Inc.
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
from nose.plugins.skip import SkipTest
from functools import wraps
from nose.plugins import Plugin
import subprocess


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

    These tests are mark with @stresstest decorator and are diabled by default.
    Use this plugin to enable these tests.
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

    WARNING: Must be used as a function call. This usage::

        @xfail
        def test_will_never_run(self):
            ...

    Will disabled the test slienly, it will never run and hide real errors in
    the code.
    """
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


def brokentest(reason):
    """
    Mark a test as broken.

    Usage::

        @brokentest("why it is broken...")
        def test_will_skip_on_failure(self):
            ...

    WARNING: Must be used as a function call. This usage::

        @brokentest
        def test_will_never_run(self):
            ...

    Will disabled the test slienly, it will never run and hide real errors in
    the code.
    """
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

    WARNING: Must be used as a function call. This usage::

        @broken_on_ci
        def test_will_never_run(self):
            ...

    Will disabled the test slienly, it will never run and hide real errors in
    the code.
    """
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
