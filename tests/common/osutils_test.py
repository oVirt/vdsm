#
# Copyright 2017 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import errno
import fcntl
import os
import select
import signal
import tempfile
import threading
import time
from multiprocessing import Process

from vdsm.common import osutils

from testlib import VdsmTestCase as TestCaseBase


class TestUninterruptiblePoll(TestCaseBase):
    RETRIES = 3
    SLEEP_INTERVAL = 0.1

    def _waitAndSigchld(self):
        time.sleep(self.SLEEP_INTERVAL)
        os.kill(os.getpid(), signal.SIGCHLD)

    def _startFakeSigchld(self):
        def _repeatFakeSigchld():
            for i in range(self.RETRIES):
                self._waitAndSigchld()
        intrThread = threading.Thread(target=_repeatFakeSigchld)
        intrThread.setDaemon(True)
        intrThread.start()

    def _noIntrWatchFd(self, fd, isEpoll, mask=select.POLLERR):
        if isEpoll:
            poller = select.epoll()
            pollInterval = self.SLEEP_INTERVAL * self.RETRIES * 2
        else:
            poller = select.poll()
            pollInterval = self.SLEEP_INTERVAL * self.RETRIES * 2 * 1000

        poller.register(fd, mask)
        osutils.uninterruptible_poll(poller.poll, pollInterval)
        poller.unregister(fd)

    def testWatchFile(self):
        tempFd, tempPath = tempfile.mkstemp()
        os.unlink(tempPath)
        self._startFakeSigchld()
        # only poll can support regular file
        self._noIntrWatchFd(tempFd, isEpoll=False)

    def testWatchPipeEpoll(self):
        myPipe, hisPipe = os.pipe()
        self._startFakeSigchld()
        self._noIntrWatchFd(myPipe, isEpoll=True)  # caught IOError

    def testWatchPipePoll(self):
        myPipe, hisPipe = os.pipe()
        self._startFakeSigchld()
        self._noIntrWatchFd(myPipe, isEpoll=False)  # caught select.error

    def testNoTimeoutPipePoll(self):
        def _sigChldAndClose(fd):
            self._waitAndSigchld()
            time.sleep(self.SLEEP_INTERVAL)
            os.close(fd)

        myPipe, hisPipe = os.pipe()

        poller = select.poll()
        poller.register(myPipe, select.POLLHUP)

        intrThread = threading.Thread(target=_sigChldAndClose, args=(hisPipe,))
        intrThread.setDaemon(True)
        intrThread.start()

        try:
            self.assertTrue(len(
                osutils.uninterruptible_poll(poller.poll, -1)) > 0)
        finally:
            os.close(myPipe)

    def testClosedPipe(self):
        def _closePipe(pipe):
            time.sleep(self.SLEEP_INTERVAL)
            os.close(pipe)

        myPipe, hisPipe = os.pipe()
        proc = Process(target=_closePipe, args=(hisPipe,))
        proc.start()
        # no exception caught
        self._noIntrWatchFd(myPipe, isEpoll=False, mask=select.POLLIN)
        proc.join()

    def testPipeWriteEAGAIN(self):
        def _raiseEAGAIN(pipe):
            PIPE_BUF_BYTES = 65536
            many_bytes = b'0' * (1 + PIPE_BUF_BYTES)
            for i in range(self.RETRIES):
                time.sleep(self.SLEEP_INTERVAL)
                try:
                    os.write(pipe, many_bytes)
                except OSError as e:
                    if e.errno not in (errno.EINTR, errno.EAGAIN):
                        raise

        myPipe, hisPipe = os.pipe()
        fcntl.fcntl(hisPipe, fcntl.F_SETFL, os.O_NONBLOCK)
        fcntl.fcntl(myPipe, fcntl.F_SETFL, os.O_NONBLOCK)
        proc = Process(target=_raiseEAGAIN, args=(hisPipe,))
        proc.start()
        self._noIntrWatchFd(myPipe, isEpoll=False, mask=select.POLLIN)
        proc.join()

    def testGetUmask(self):
        # Retrieve umask in a not thread-safe way.
        os_umask = os.umask(0)
        os.umask(os_umask)

        # Compare vs. a thread-safe implementation.
        self.assertEqual(oct(os_umask), oct(osutils.get_umask()))
