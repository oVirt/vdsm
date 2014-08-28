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

from vdsm import utils


modprobe = utils.CommandPath("modprobe",
                             "/sbin/modprobe",      # EL6
                             "/usr/sbin/modprobe",  # Fedora
                             )


class SlowTestsPlugin(Plugin):
    """Skips tests that might be too slow to be run for quick iteration
    builds"""
    name = 'slowtests'
    enabled = False

    def add_options(self, parser, env=os.environ):
        env_opt = 'NOSE_SKIP_SLOW_TESTS'
        if env is None:
            default = False
        else:
            default = env.get(env_opt)

        parser.add_option('--without-slow-tests',
                          action='store_true',
                          default=default,
                          dest='disable_slow_tests',
                          help='Some tests might take a long time to run, ' +
                               'use this to skip slow tests automatically.' +
                               '  [%s]' % env_opt)

    def configure(self, options, conf):
        Plugin.configure(self, options, conf)
        if options.disable_slow_tests:
            SlowTestsPlugin.enabled = True


class StressTestsPlugin(Plugin):
    """
    Denotes a test which stresses the resources of the system under test. Such
    tests should probably not be run in parallel.  This plugin provides a
    mechanism for parallel testing applications to skip stress tests.
    """
    name = 'nonparalleltests'
    enabled = False

    def add_options(self, parser, env=os.environ):
        env_opt = 'NOSE_SKIP_STRESS_TESTS'
        if env is None:
            default = False
        else:
            default = env.get(env_opt)

        parser.add_option('--without-stress-tests',
                          action='store_true',
                          default=default,
                          dest='disable_stress_tests',
                          help='Some tests stress the resources of the ' +
                               'system under test.  Use this option to skip' +
                               'these tests (eg. when doing parallel' +
                               'testing [%s]' % env_opt)

    def configure(self, options, conf):
        Plugin.configure(self, options, conf)
        if options.disable_stress_tests:
            StressTestsPlugin.enabled = True


def ValidateRunningAsRoot(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if os.geteuid() != 0:
            raise SkipTest("This test must be run as root")

        return f(*args, **kwargs)

    return wrapper


def RequireDummyMod(f):
    """
    Assumes root privileges to be used after
    ValidateRunningAsRoot decoration.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not os.path.exists('/sys/module/dummy'):
            cmd_modprobe = [modprobe.cmd, "dummy"]
            rc, out, err = utils.execCmd(cmd_modprobe, sudo=True)
        return f(*args, **kwargs)
    return wrapper


def RequireBondingMod(f):
    """
    Assumes root privileges to be used after
    ValidateRunningAsRoot decoration.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not os.path.exists('/sys/module/bonding'):
            cmd_modprobe = [modprobe.cmd, "bonding"]
            rc, out, err = utils.execCmd(cmd_modprobe, sudo=True)
        return f(*args, **kwargs)

    return wrapper


def RequireVethMod(f):
    """
    Assumes root privileges to be used after
    ValidateRunningAsRoot decoration.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not os.path.exists('/sys/module/veth'):
            cmd_modprobe = [modprobe.cmd, "veth"]
            rc, out, err = utils.execCmd(cmd_modprobe, sudo=True)
        return f(*args, **kwargs)
    return wrapper


def slowtest(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if SlowTestsPlugin.enabled:
            raise SkipTest("Slow tests have been disabled")

        return f(*args, **kwargs)

    return wrapper


def brokentest(msg="Test failed but it is known to be broken"):
    def wrap(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except:
                raise SkipTest(msg)
        return wrapper

    return wrap


def stresstest(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if StressTestsPlugin.enabled:
            raise SkipTest("Stress tests have been disabled")

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
