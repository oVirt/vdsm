# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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


def trigger(
    attr_matches=(),
    property_matches=(),
    subsystem_matches=(),
    path=None,
):
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

    path                Path to trigger events for. For example:
                        /dev/mapper/20024f4005854000a

    '''
    _run_command(['control', '--reload'])

    cmd = ['trigger', '--verbose', '--action', 'change']

    for name, value in property_matches:
        cmd.append('--property-match={}={}'.format(name, value))

    for name, value in attr_matches:
        cmd.append('--attr-match={}={}'.format(name, value))

    for name in subsystem_matches:
        cmd.append('--subsystem-match={}'.format(name))

    if path:
        cmd.append(path)

    _run_command(cmd)


def info(device):
    out = _run_command(['info', '--query=property', device])
    return out.decode('utf-8')


def _run_command(args):
    cmd = [_UDEVADM.cmd]
    cmd.extend(args)
    return commands.run(cmd)
