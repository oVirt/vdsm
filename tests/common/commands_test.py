# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import logging
import os
import os.path
import signal
import subprocess
import sys
import threading
import time

from unittest import mock

import pytest

from vdsm import utils
from vdsm.common import cmdutils
from vdsm.common import constants
from vdsm.common import commands
from vdsm.common.password import ProtectedPassword
from vdsm.storage import constants as sc

import fakelib


class TestStart:

    def test_true(self):
        p = commands.start(["true"])
        out, err = p.communicate()
        assert p.returncode == 0
        assert out is None
        assert err is None

    def test_false(self):
        p = commands.start(["false"])
        out, err = p.communicate()
        assert p.returncode == 1
        assert out is None
        assert err is None

    def test_out(self):
        p = commands.start(
            ["echo", "-n", "out"],
            stdout=subprocess.PIPE)
        out, err = p.communicate()
        assert out == b"out"
        assert err is None

    def test_out_err(self):
        p = commands.start(
            ["sh", "-c", "echo -n out >&1; echo -n err >&2"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        out, err = p.communicate()
        assert out == b"out"
        assert err == b"err"

    def test_in_out(self):
        p = commands.start(
            ["cat"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE)
        out, err = p.communicate(b"data")
        assert out == b"data"
        assert err is None

    def test_in_err(self):
        p = commands.start(
            ["sh", "-c", "cat >&2"],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE)
        out, err = p.communicate(b"data")
        assert out is None
        assert err == b"data"

    def test_start_nonexisting(self):
        with pytest.raises(OSError):
            args = ["doesn't exist"]
            commands.start(args, reset_cpu_affinity=False)

    def test_child_terminated(self):
        p = commands.start(["sleep", "1"])
        commands.run(["kill", "-%d" % signal.SIGTERM, "%d" % p.pid])
        assert p.wait() == -signal.SIGTERM

    def test_terminate(self):
        p = commands.start(["sleep", "1"])
        p.terminate()
        assert p.wait() == -signal.SIGTERM

    def test_kill(self):
        p = commands.start(["sleep", "1"])
        p.kill()
        assert p.wait() == -signal.SIGKILL

    def test_start_should_log_args(self, monkeypatch):
        monkeypatch.setattr(cmdutils, "command_log_line", mock.MagicMock())
        monkeypatch.setattr(commands, "log", fakelib.FakeLogger())
        cmdutils.command_log_line.return_value = "zorro"
        args = ["true"]
        commands.start(args)
        assert (logging.DEBUG, "zorro", {}) in commands.log.messages

    @pytest.mark.skipif(os.geteuid() != 0, reason="Requires root")
    def test_sudo_kill(self):
        p = commands.start(["sleep", "1"], sudo=True)
        p.kill()
        assert p.wait() == -signal.SIGKILL

    @pytest.mark.skipif(os.geteuid() != 0, reason="Requires root")
    def test_sudo_terminate(self):
        p = commands.start(["sleep", "1"], sudo=True)
        p.terminate()
        assert p.wait() == -signal.SIGTERM


class TestCommunicate:

    def test_out(self):
        p = commands.start(["echo", "-n", "it works"], stdout=subprocess.PIPE)
        out, err = commands.communicate(p)
        assert p.returncode == 0
        assert out == b"it works"
        assert err is None

    def test_err(self):
        cmd = ["sh", "-c", "echo -n fail >&2; exit 1"]
        p = commands.start(cmd, stderr=subprocess.PIPE)
        out, err = commands.communicate(p)
        assert p.returncode == 1
        assert out is None
        assert err == b"fail"

    def test_int_out(self):
        cmd = ["cat"]
        p = commands.start(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        out, err = commands.communicate(p, b"data")
        assert p.returncode == 0
        assert out == b"data"
        assert err is None

    def test_in_err(self):
        cmd = ["sh", "-c", "cat >&2; exit 1"]
        p = commands.start(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = commands.communicate(p, b"data")
        assert p.returncode == 1
        assert out is None
        assert err == b"data"

    def test_out_err(self):
        cmd = ["sh", "-c", "echo -n 'test out' >&1; echo -n fail >&2; exit 1"]
        p = commands.start(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = commands.communicate(p)
        assert p.returncode == 1
        assert out == b"test out"
        assert err == b"fail"


class TestRun:

    def test_run(self):
        assert commands.run(["true"]) == b""

    def test_run_out(self):
        out = commands.run(["echo", "-n", "out"])
        assert out == b"out"

    def test_run_out_err(self):
        out = commands.run(["sh", "-c", "echo -n out >&1; echo -n err >&2"])
        assert out == b"out"

    def test_run_input(self):
        out = commands.run(["cat"], input=b"data")
        assert out == b"data"

    def test_run_nonexisting(self):
        with pytest.raises(OSError):
            commands.run(["doesn't exist"], reset_cpu_affinity=False)

    def test_run_error(self):
        with pytest.raises(cmdutils.Error) as e:
            commands.run(["false"])
        assert e.value.rc == 1
        assert e.value.out == b""
        assert e.value.err == b""

    def test_run_error_data(self):
        with pytest.raises(cmdutils.Error) as e:
            args = ["sh", "-c", "echo -n out >&1; echo -n err >&2; exit 1"]
            commands.run(args)
        assert e.value.rc == 1
        assert e.value.out == b"out"
        assert e.value.err == b"err"

    def test_setsid(self):
        args = [sys.executable, '-c',
                'from __future__ import print_function;'
                'import os;'
                'print(os.getsid(os.getpid()))']
        out = commands.run(args, setsid=True)
        assert int(out) != os.getsid(os.getpid())

    def test_ioclass(self):
        out = commands.run(
            ['ionice'],
            ioclass=utils.IOCLASS.BEST_EFFORT,
            ioclassdata=3)
        assert out.strip() == b"best-effort: prio 3"

    def test_nice(self):
        out = commands.run(["cat", "/proc/self/stat"], nice=7)
        assert int(out.split()[18]) == 7

    def test_unprotect_passwords(self):
        secret = ProtectedPassword("top-secret")
        args = ["echo", "-n", secret]
        out = commands.run(args)
        assert out.decode() == secret.value

    def test_protect_log_passwords(self, monkeypatch):
        monkeypatch.setattr(commands, "log", fakelib.FakeLogger())
        secret = ProtectedPassword("top-secret")
        args = ["echo", "-n", secret]
        commands.run(args)
        for level, msg, kwargs in commands.log.messages:
            assert str(secret.value) not in msg

    def test_protect_password_error(self):
        secret = ProtectedPassword("top-secret")
        args = ["false", secret]
        with pytest.raises(cmdutils.Error) as e:
            commands.run(args)
        assert secret.value not in str(e.value)

    def test_run_should_log_result(self, monkeypatch):
        monkeypatch.setattr(commands, "log", fakelib.FakeLogger())
        monkeypatch.setattr(cmdutils, "command_log_line", mock.MagicMock())
        monkeypatch.setattr(cmdutils, "retcode_log_line", mock.MagicMock())
        cmdutils.command_log_line.return_value = "log line"
        cmdutils.retcode_log_line.return_value = "error line"
        args = ["false"]
        try:
            commands.run(args)
        except cmdutils.Error:
            pass
        assert (logging.DEBUG, "log line", {}) in commands.log.messages
        assert (logging.DEBUG, "error line", {}) in commands.log.messages

    @pytest.mark.skipif(os.geteuid() != 0, reason="Requires root")
    def test_run_sudo(self):
        assert commands.run(["whoami"], sudo=True) == b"root\n"


class TestExecCmd:
    CMD_TYPES = [tuple, list, iter]

    @pytest.mark.parametrize("cmd", CMD_TYPES)
    def test_normal(self, cmd):
        rc, out, _ = commands.execCmd(cmd(('echo', 'hello world')))
        assert rc == 0
        assert out[0].decode() == 'hello world'

    @pytest.mark.parametrize("cmd", CMD_TYPES)
    def test_io_class(self, cmd):
        rc, out, _ = commands.execCmd(cmd(('ionice',)), ioclass=2,
                                      ioclassdata=3)
        assert rc == 0
        assert out[0].decode().strip() == 'best-effort: prio 3'

    @pytest.mark.parametrize("cmd", CMD_TYPES)
    def test_nice(self, cmd):
        rc, out, _ = commands.execCmd(cmd(('cat', '/proc/self/stat')), nice=7)
        assert rc == 0
        assert int(out[0].split()[18]) == 7

    @pytest.mark.parametrize("cmd", CMD_TYPES)
    def test_set_sid(self, cmd):
        cmd_args = (sys.executable, '-c',
                    'from __future__ import print_function;import os;'
                    'print(os.getsid(os.getpid()))')
        rc, out, _ = commands.execCmd(cmd(cmd_args), setsid=True)
        assert int(out[0]) != os.getsid(os.getpid())

    @pytest.mark.parametrize("cmd", CMD_TYPES)
    @pytest.mark.skipif(os.getuid() != 0, reason="Requires root")
    def test_sudo(self, cmd):
        rc, out, _ = commands.execCmd(cmd(('grep',
                                      'Uid', '/proc/self/status')),
                                      sudo=True)
        assert rc == 0
        assert int(out[0].split()[2]) == 0


class TestExecCmdStress:

    CONCURRENCY = 50
    FUNC_DELAY = 0.01
    FUNC_CALLS = 40
    BLOCK_SIZE = sc.BLOCK_SIZE_4K
    BLOCK_COUNT = 256

    def setup_method(self, test_method):
        self.data = None  # Written to process stdin
        self.workers = []
        self.resume = threading.Event()

    @pytest.mark.stress
    def test_read_stderr(self):
        self.check(self.read_stderr)

    @pytest.mark.stress
    def test_read_stdout_stderr(self):
        self.check(self.read_stdout_stderr)

    @pytest.mark.stress
    def test_write_stdin_read_stderr(self):
        self.data = 'x' * self.BLOCK_SIZE * self.BLOCK_COUNT
        self.check(self.write_stdin_read_stderr)

    def check(self, func):
        for i in range(self.CONCURRENCY):
            worker = Worker(self.resume, func, self.FUNC_CALLS,
                            self.FUNC_DELAY)
            self.workers.append(worker)
            worker.start()
        for worker in self.workers:
            worker.wait()
        self.resume.set()
        for worker in self.workers:
            worker.join()
        for worker in self.workers:
            if worker.exc_info:
                t, v, tb = worker.exc_info
                if v is None:
                    v = t()
                if v.__traceback__ is not tb:
                    raise v.with_traceback(tb)
                raise v

    def read_stderr(self):
        args = ['if=/dev/zero',
                'of=/dev/null',
                'bs=%d' % self.BLOCK_SIZE,
                'count=%d' % self.BLOCK_COUNT]
        self.run_dd(args)

    def read_stdout_stderr(self):
        args = ['if=/dev/zero',
                'bs=%d' % self.BLOCK_SIZE,
                'count=%d' % self.BLOCK_COUNT]
        out = self.run_dd(args)
        size = self.BLOCK_SIZE * self.BLOCK_COUNT
        assert len(out) == size, "Partial read: {}/{}".format(len(out), size)

    def write_stdin_read_stderr(self):
        args = ['of=/dev/null',
                'bs=%d' % self.BLOCK_SIZE,
                'count=%d' % self.BLOCK_COUNT]
        self.run_dd(args)

    def run_dd(self, args):
        cmd = [constants.EXT_DD]
        cmd.extend(args)
        rc, out, err = commands.execCmd(cmd, raw=True, data=self.data)
        assert rc == 0, "Process failed: rc={} err={}".format(rc, err)
        assert err != '', "No data from stderr"
        return out


class Worker(object):

    def __init__(self, resume, func, func_calls, func_delay):
        self.exc_info = None
        self._resume = resume
        self._func = func
        self._func_calls = func_calls
        self._func_delay = func_delay
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True

    def start(self):
        self._thread.start()

    def wait(self):
        self._ready.wait()

    def join(self):
        self._thread.join()

    def _run(self):
        try:
            self._ready.set()
            self._resume.wait()
            for n in range(self._func_calls):
                self._func()
                time.sleep(self._func_delay)
        except Exception:
            self.exc_info = sys.exc_info()


class TestWaitAsync:

    def test_normal_termination(self):
        event = threading.Event()
        p = commands.start(["sleep", "0.1"])

        # Start async waiter waiting for normal terminatin.
        commands.wait_async(p, event=event)

        if not event.wait(1):
            raise RuntimeError("Error waiting for termination")

        assert p.returncode == 0

    def test_terminate_async(self):
        event = threading.Event()
        p = commands.start(["sleep", "10"])

        # Terminate the command without waiting for it, and start async waiter.
        p.terminate()
        commands.wait_async(p, event=event)

        if not event.wait(1):
            raise RuntimeError("Error waiting for termination")

        assert p.returncode == -15

    def test_out_err(self):
        event = threading.Event()
        p = commands.start(
            ["sh", "-c", "echo out>&1; echo err>&2; sleep 0.1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)

        commands.wait_async(p, event=event)

        if not event.wait(1):
            raise RuntimeError("Error waiting for termination")

        assert p.returncode == 0
