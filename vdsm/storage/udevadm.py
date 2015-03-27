#
# Copyright 2015 Red Hat, Inc.
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

from vdsm import utils

_UDEVADM = utils.CommandPath("udevadm", "/sbin/udevadm", "/usr/sbin/udevadm")


class Error(Exception):

    def __init__(self, rc, err):
        self.rc = rc
        self.err = err

    def __str__(self):
        return "Process failed with rc=%d err=%r" % (self.rc, self.err)


def settle(timeout, exit_if_exists=None):
    """
    Watches the udev event queue, and wait until all current events are
    handled.

    Arguments:

    timeout        Maximum number of seconds to wait for the event queue to
                   become empty. A value of 0 will check if the queue is empty
                   and always return immediately.

    exit_if_exists Stop waiting if file exists.

    Raise udevadm.Error if process failed.
    """
    args = ["settle", "--timeout=%s" % timeout]

    if exit_if_exists:
        args.append("--exit-if-exists=%s" % exit_if_exists)

    _run_command(args)


def _run_command(args):
    cmd = [_UDEVADM.cmd]
    cmd.extend(args)
    rc, out, err = utils.execCmd(cmd)
    if rc != 0:
        raise Error(rc, err)
