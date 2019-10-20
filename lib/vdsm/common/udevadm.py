#
# Copyright 2015-2017 Red Hat, Inc.
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
import logging

from vdsm.common import cmdutils
from vdsm.common import commands

_UDEVADM = cmdutils.CommandPath(
    "udevadm", "/sbin/udevadm", "/usr/sbin/udevadm")


def settle(timeout, exit_if_exists=None):
    """
    Watches the udev event queue, and wait until all current events are
    handled.

    Arguments:

    timeout        Maximum number of seconds to wait for the event queue to
                   become empty. A value of 0 will check if the queue is empty
                   and always return immediately.

    exit_if_exists Stop waiting if file exists.
    """
    args = ["settle", "--timeout=%s" % timeout]

    if exit_if_exists:
        args.append("--exit-if-exists=%s" % exit_if_exists)

    try:
        _run_command(args)
    except cmdutils.Error as e:
        logging.error("%s", e)


def trigger(attr_matches=(), property_matches=(), subsystem_matches=()):
    '''
    Request device events from the kernel.

    Arguments:

    attr_matches        List of 2-tuples that contain attribute name and
                        it's value. These are expanded like this:

                        [('a', 'b'), ('c', 'd')] ~>
                        --attr-match=a=b --attr-match=c=d

                        and causes only events from devices that match
                        given attributes to be triggered.

    property_matches    Similar to attr_matches. Expects list of 2-tuples
                        that expand in similar fashion, that is

                        [('a', 'b'), ('c', 'd')] ~>
                        --property-match=a=b --property-match=c=d

                        and causes only events from devices that match
                        given property to be triggered.

    subsystem_matches   Expects an iterable of subsystems.

                        ('a', 'b') ~> --subsystem-match=a --subsystem-match=b

                        Causes only events related to specified subsystem to
                        be triggered.
    '''
    _run_command(['control', '--reload'])

    cmd = ['trigger', '--verbose', '--action', 'change']

    for name, value in property_matches:
        cmd.append('--property-match={}={}'.format(name, value))

    for name, value in attr_matches:
        cmd.append('--attr-match={}={}'.format(name, value))

    for name in subsystem_matches:
        cmd.append('--subsystem-match={}'.format(name))

    _run_command(cmd)


def _run_command(args):
    cmd = [_UDEVADM.cmd]
    cmd.extend(args)
    commands.run(cmd)
