# Copyright 2014-2016 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

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
