# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import functools
import signal
import subprocess
import sys
import time
import threading

from vdsm.common import sigutils


def child_test(register=True):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if register:
                sigutils.register()
            sys.stdout.write('ready\n')
            try:
                return func(*args, **kwargs)
            finally:
                sys.stdout.write('done\n')
        return wrapper
    return decorator


@child_test()
def check_signal_received():
    sigutils.wait_for_signal()


@child_test()
def check_signal_timeout(timeout):
    sigutils.wait_for_signal(float(timeout))


@child_test()
def check_signal_times():
    sigutils.wait_for_signal()
    sys.stdout.write('woke up\n')
    sigutils.wait_for_signal()
    sys.stdout.write('woke up\n')
    sigutils.wait_for_signal()
    sys.stdout.write('woke up\n')


@child_test()
def check_child_signal_to_thread():
    '''
    This test checks the following scenario:
    * main thread is waiting for signal
    * another thread is running a subprocess
    * the subprocess exits before another thread dies

    This leads to signal being sent to another thread but not the main thread.
    This test makes sure main thread is woken up.
    '''

    def thread_target():
        subprocess.Popen(['true'])
        time.sleep(1)  # sleep so SIGCHLD is delivered here.
    threading.Thread(target=thread_target).start()
    sigutils.wait_for_signal()


@child_test()
def check_register_twice():
    try:
        sigutils.register()
    except RuntimeError:
        sys.stdout.write('exception\n')


@child_test(register=False)
def check_uninitialized():
    try:
        sigutils.wait_for_signal()
    except RuntimeError:
        sys.stdout.write('exception\n')


if __name__ == '__main__':
    # Set up signal handlers
    signal.signal(signal.SIGUSR1,
                  lambda *_: sys.stdout.write('signal sigusr1\n'))
    signal.signal(signal.SIGCHLD,
                  lambda *_: sys.stdout.write('signal sigchld\n'))

    # Set timer to kill the process in case we're stuck.
    signal.signal(signal.SIGALRM, lambda *_: sys.exit(1))
    signal.setitimer(signal.ITIMER_REAL, 10)

    globals()[sys.argv[1]](*sys.argv[2:])
