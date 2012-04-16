#
# Copyright 2009-2011 Red Hat, Inc.
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
import os
from nose.plugins.skip import SkipTest
from functools import wraps
from nose.plugins import Plugin
import subprocess


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


def ValidateRunningAsRoot(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if os.geteuid() != 0:
            raise SkipTest("This test must be run as root")

        return f(*args, **kwargs)

    return wrapper


def slowtest(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if SlowTestsPlugin.enabled:
            raise SkipTest("Slow tests have been disabled")

        return f(*args, **kwargs)

    return wrapper


def checkSudo(cmd):
    p = subprocess.Popen(['sudo', '-l', '-n'] + cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
    out, err = p.communicate()

    if p.returncode != 0:
        raise SkipTest("Test requires SUDO configuration (%s)" % err.strip())
