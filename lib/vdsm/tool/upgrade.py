# Copyright 2013-2014 Red Hat, Inc.
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

import argparse
import logging
import logging.config
import os

from ..constants import P_VDSM_LIB
from ..utils import touchFile, isOvirtNode


def _get_upgrade_log():
    return logging.getLogger('upgrade')


def _upgrade_seal_path(upgrade):
    return os.path.join(P_VDSM_LIB, 'upgrade', upgrade.name)


def _upgrade_needed(upgrade):
    return not os.path.exists(_upgrade_seal_path(upgrade))


def _upgrade_seal(upgrade):
    seal_file = _upgrade_seal_path(upgrade)
    try:
        touchFile(seal_file)
    except (OSError, IOError):
        _get_upgrade_log().exception("Failed to seal upgrade %s", upgrade.name)
    else:
        if isOvirtNode():
            from ovirt.node.utils import fs
            fs.Config().persist(seal_file)
        _get_upgrade_log().debug("Upgrade %s successfully performed",
                                 upgrade.name)


def apply_upgrade(upgrade, *args):
    """
    This function operates on an upgrade object, that follows an interface
    defined below. This function parses the arguments, checks if upgrade should
    run, and upon successful completion calls _upgrade_seal() that marks
    upgrade as done, to avoid executing it again. It also allows the upgrade
    to define additional arguments.

    apply_upgrade works on an upgrade object, that exposes the following
    interface:

    upgrade.name - unique name of the upgrade

    upgrade.run(ns, args) - run the upgrade.
    Params:
        ns - namespace received from ArgumentParser.parse_known_args()
        args - arguments received from ArgumentParser.parse_known_args()

    upgrade.extendArgParser(argParser) - extend upgrade manager's arg-parse
    with upgrade specific params. Optional.
    Params:
        argParser - instance of ArgumentParser to work on.

    """
    argparser = argparse.ArgumentParser('vdsm-tool %s' % args[0])
    argparser.add_argument(
        '--run-again',
        dest='runAgain',
        default=False,
        action='store_true',
        help='Run the upgrade again, even if it was ran before'
    )
    if hasattr(upgrade, 'extendArgParser'):
        upgrade.extendArgParser(argparser)
    ns, args = argparser.parse_known_args(args[1:])
    if (_upgrade_needed(upgrade) or ns.runAgain):
        _get_upgrade_log().debug("Running upgrade %s", upgrade.name)
        try:
            upgrade.run(ns, args)
        except Exception:
            _get_upgrade_log().exception("Failed to run %s", upgrade.name)
            return 1
        else:
            _upgrade_seal(upgrade)
    return 0
