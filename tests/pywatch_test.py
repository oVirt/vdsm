# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import errno
import logging
import os
import signal
import subprocess
import sys

import pytest

from vdsm.common import cache
from vdsm.common import cmdutils
from vdsm.common import commands


log = logging.getLogger("test")


def package_version(pkg_name):
    """
    Query package version info and return "version-release" string, or empty
    string if the query fails.
    """
    # Using slow "rpm -qa" since on CentOS 7 "rpm -q" always succeeds and
    # writes "package python2 is not installed" to stdout, while "rpm -qa"
    # returns empty string if the package is not installed.
    out = commands.run(
        ["rpm", "-qa", "--queryformat", "%{VERSION}-%{RELEASE}", pkg_name])
    return out.decode("utf-8")


@cache.memoized
def has_py_gdb_support():
    """
    Return True if python-debuginfo package is installed and has the same
    version-release as python package.
    """
    pkg_name = "python{}".format(sys.version_info.major)
    pkg_ver = package_version(pkg_name)

    pkg_dbg_ver = package_version("{}-debuginfo".format(pkg_name))

    # On CentOS Stream 9 debuginfo packages now include the minor version in
    # the name, i.e. python3.9-debuginfo-...
    if pkg_dbg_ver == "":
        pkg_name2 = "python{}.{}".format(sys.version_info.major,
                                         sys.version_info.minor)
        pkg_dbg_ver = package_version("{}-debuginfo".format(pkg_name2))

    return pkg_dbg_ver != "" and pkg_dbg_ver == pkg_ver


class TestPyWatch(object):

    def test_short_success(self):
        commands.run([sys.executable, 'py-watch', '0.2', 'true'])

    def test_short_failure(self):
        with pytest.raises(cmdutils.Error) as e:
            commands.run([sys.executable, 'py-watch', '0.2', 'false'])
        assert e.value.rc == 1

    def test_timeout_output(self):
        with pytest.raises(cmdutils.Error) as e:
            commands.run([sys.executable, 'py-watch', '0.1', 'sleep', '10'])
        assert b'Watched process timed out' in e.value.out
        assert b'Terminating watched process' in e.value.out
        assert e.value.rc == 128 + signal.SIGTERM

    @pytest.mark.xfail(
        not has_py_gdb_support(),
        reason=("gdb support missing - python debuginfo package unavailable "
                "or has wrong version")
    )
    def test_timeout_backtrace(self):
        script = '''
import time

def outer():
    inner()

def inner():
    time.sleep(10)

outer()
'''
        with pytest.raises(cmdutils.Error) as e:
            commands.run([
                sys.executable, 'py-watch', '0.1', sys.executable,
                '-c', script])
        assert b'line 8, in inner' in e.value.out
        assert b'line 5, in outer' in e.value.out

    def test_kill_grandkids(self):
        # watch a bash process that starts a grandchild bash process.
        # The grandkid bash echoes its pid and sleeps a lot.
        # on timeout, py-watch should kill the process session spawned by it.
        with pytest.raises(cmdutils.Error) as e:
            commands.run([
                sys.executable, 'py-watch', '1.0', 'bash',
                '-c', 'bash -c "readlink /proc/self; sleep 10"'])

        # assert that the internal bash no longer exist
        grandkid_pid = int(e.value.out.splitlines()[0])
        with pytest.raises(OSError) as e:
            assert os.kill(grandkid_pid, 0)
        assert e.value.errno == errno.ESRCH

    @pytest.mark.parametrize("signo", [signal.SIGINT, signal.SIGTERM])
    def test_terminate(self, signo):
        # Start bash process printing its pid and sleeping. The short sleep
        # before printing the pid avoids a race when we got the pid before
        # py-watch started to wait for the child.
        p = subprocess.Popen(
            [sys.executable, 'py-watch', '10', 'bash', '-c',
                'sleep 0.5; echo $$; sleep 10'],
            stdout=subprocess.PIPE)

        # Wait until the underlying bash process prints its pid.
        for src, data in cmdutils.receive(p):
            if src == cmdutils.OUT:
                child_pid = int(data)
                break

        # Terminate py-watch, and check its exit code.
        p.send_signal(signo)
        assert p.wait() == 128 + signo

        # Check that the child process was terminated.
        with pytest.raises(OSError) as e:
            assert os.kill(child_pid, 0)
        assert e.value.errno == errno.ESRCH
