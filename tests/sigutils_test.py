# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import contextlib
import errno
import os
import signal
import subprocess
import sys
import time

from testValidation import broken_on_ci
from testlib import VdsmTestCase

CHILD_SCRIPT = os.path.abspath('tests_child.py')


def assert_read(stream, expected):
    while True:
        try:
            assert stream.read(len(expected)) == expected
        except IOError as e:
            if e.errno != errno.EINTR:
                raise
        else:
            break


@contextlib.contextmanager
def child_test(*args):
    proc = subprocess.Popen(
        [sys.executable, "-u", CHILD_SCRIPT] + list(args),
        stdout=subprocess.PIPE,
        cwd=os.path.dirname(__file__)
    )
    try:
        yield proc
    finally:
        proc.wait()


class TestSigutils(VdsmTestCase):
    def test_signal_received(self):
        with child_test('check_signal_received') as child:
            assert_read(child.stdout, b'ready\n')
            child.send_signal(signal.SIGUSR1)
            assert_read(child.stdout, b'signal sigusr1\n')
            assert_read(child.stdout, b'done\n')

    @broken_on_ci("timing sensitive")
    def test_signal_timeout(self):
        TIMEOUT = 0.2
        with child_test('check_signal_timeout', str(TIMEOUT)) as child:
            now = time.time()
            assert_read(child.stdout, b'ready\n')
            assert_read(child.stdout, b'done\n')
            later = time.time()

            # 3 is a safety factor
            assert TIMEOUT < (later - now) < TIMEOUT * 3

    def test_signal_3_times(self):
        '''
        A sanity test to make sure wait_for_signal fires more than once.
        '''
        with child_test('check_signal_times') as child:
            assert_read(child.stdout, b'ready\n')
            child.send_signal(signal.SIGUSR1)
            assert_read(child.stdout, b'signal sigusr1\n')
            assert_read(child.stdout, b'woke up\n')
            child.send_signal(signal.SIGUSR1)
            assert_read(child.stdout, b'signal sigusr1\n')
            assert_read(child.stdout, b'woke up\n')
            child.send_signal(signal.SIGUSR1)
            assert_read(child.stdout, b'signal sigusr1\n')
            assert_read(child.stdout, b'woke up\n')
            assert_read(child.stdout, b'done\n')

    def test_signal_to_thread(self):
        with child_test('check_child_signal_to_thread') as child:
            assert_read(child.stdout, b'ready\n')
            assert_read(child.stdout, b'signal sigchld\n')
            assert_read(child.stdout, b'done\n')

    def test_uninitialized(self):
        with child_test('check_uninitialized') as child:
            assert_read(child.stdout, b'ready\n')
            assert_read(child.stdout, b'exception\n')
            assert_read(child.stdout, b'done\n')

    def test_register_twice(self):
        with child_test('check_register_twice') as child:
            assert_read(child.stdout, b'ready\n')
            assert_read(child.stdout, b'exception\n')
            assert_read(child.stdout, b'done\n')
