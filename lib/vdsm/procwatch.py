#
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

import io
import os
import select

from vdsm import utils


class CommandStream(object):

    def __init__(self, command, stdoutcb, stderrcb):
        self._command = command
        self._poll = select.epoll()
        self._iocb = {}

        # In case both stderr and stdout are using the same fd the
        # output is squashed to the stdout (given the order of the
        # entries in the dictionary)
        self._iocb[self._command.stderr.fileno()] = stderrcb
        self._iocb[self._command.stdout.fileno()] = stdoutcb

        for fd in self._iocb:
            self._poll.register(fd, select.EPOLLIN)

    def _poll_input(self, fileno):
        self._iocb[fileno](os.read(fileno, io.DEFAULT_BUFFER_SIZE))

    def _poll_event(self, fileno):
        self._poll.unregister(fileno)
        del self._iocb[fileno]

    def _poll_timeout(self, timeout):
        # TODO: Kill NoIntrPoll, stopping polling on EINTR and procesing events
        # is good enough, we don't need to check timeout here, caller is
        # checking if timeout has expired.
        fdevents = utils.NoIntrPoll(self._poll.poll, timeout)

        for fileno, event in fdevents:
            if event & select.EPOLLIN:
                self._poll_input(fileno)
            elif event & (select.EPOLLHUP | select.EPOLLERR):
                self._poll_event(fileno)

    @property
    def closed(self):
        return len(self._iocb) == 0

    def receive(self, timeout=None):
        """
        Receiving data from the command can raise OSError
        exceptions as described in read(2).
        """
        if timeout is None:
            poll_remaining = -1
        else:
            endtime = utils.monotonic_time() + timeout

        while not self.closed:
            if timeout is not None:
                poll_remaining = endtime - utils.monotonic_time()
                if poll_remaining <= 0:
                    break

            self._poll_timeout(poll_remaining)
