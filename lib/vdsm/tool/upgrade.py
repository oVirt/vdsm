# Copyright 2013 Red Hat, Inc.
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
from functools import wraps
import logging
import logging.config
from logging.handlers import SysLogHandler
import os
import sys

from ..constants import (P_VDSM_LOG, P_VDSM_LIB, P_VDSM_CONF, VDSM_USER,
                         VDSM_GROUP)
from ..utils import touchFile

sys.path.append("/usr/share/vdsm")
from storage.fileUtils import chown


class Upgrade(object):
    """
        Code that needs to be run exactly once can use this class as
        a general, simplistic mechanism. Usage:

        with Upgrade('someUpgrade') as upgrade:
            if upgrade.isNeeded():
                runSomeCode()
                upgrade.seal()

        Upgrade as a context manager automatically redirects logging
        to the upgrade log. It also supplies the isNeeded and seal methods.
        To call isNeeded and seal automatically use this module's upgrade
        decorator.
    """
    def __init__(self, upgradeName):
        self._upgradeName = upgradeName
        self._upgradeFilePath = os.path.join(P_VDSM_LIB,
                                             'upgrade', self._upgradeName)
        try:
            # First load normal VDSM loggers, then the upgrade logger.
            # This will override VDSM's root logger but will keep the other
            # loggers intact. During an upgrade we add the update handler
            # to all loggers.
            logging.config.fileConfig(P_VDSM_CONF + 'logger.conf',
                                      disable_existing_loggers=False)
            logging.config.fileConfig(P_VDSM_CONF + 'upgrade.logger.conf',
                                      disable_existing_loggers=False)
            chown(
                os.path.join(P_VDSM_LOG, 'upgrade.log'),
                VDSM_USER,
                VDSM_GROUP)
        except Exception:
            logging.getLogger('upgrade').addHandler(SysLogHandler('/dev/log'))
            logging.exception("Could not init proper logging")
        finally:
            self.log = logging.getLogger('upgrade')

    def isNeeded(self):
        return not os.path.exists(self._upgradeFilePath)

    def seal(self):
        """
            Mark the upgrade as a success
        """
        try:
            touchFile(self._upgradeFilePath)
        except (OSError, IOError):
            self.log.exception("Failed to seal upgrade %s", self._upgradeName)
        else:
            self.log.debug("Upgrade %s successfully performed",
                           self._upgradeName)

    def _attachUpgradeLog(self):
        self._editOtherLoggers(logging.Logger.addHandler)

    def _detachUpgradeLog(self):
        self._editOtherLoggers(logging.Logger.removeHandler)

    def _editOtherLoggers(self, edit):
        """
        add/remove upgrade handler to/from all non-upgrade loggers
        """
        loggers = dict(logging.Logger.manager.loggerDict.items() +
                       [('root', logging.getLogger())])
        for name, logger in loggers.iteritems():
            if name != 'upgrade':
                for handler in logging.getLogger('upgrade').handlers:
                    try:
                        edit(logger, handler)  # Call logger.edit(handler)
                    except TypeError:
                        pass

    def __enter__(self):
        """
        All logging will *also* be output to the upgrade log during
        the scope of this Upgrade.
        """
        self._attachUpgradeLog()
        return self

    def __exit__(self, type, value, traceback):
        self._detachUpgradeLog()


def _parse_args(upgradeName):
    parser = argparse.ArgumentParser('vdsm-tool %s' % upgradeName)
    parser.add_argument(
        '--run-again',
        dest='runAgain',
        default=False,
        action='store_true',
        help='Run the upgrade again, even if it was ran before',
    )
    return parser.parse_args(sys.argv[2:])


def upgrade(upgradeName):
    """
    Used as a decorator for upgrades. Runs the upgrade with an Upgrade
    context manager (documented above). Automatically calls isNeeded and seal
    as needed.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            with Upgrade(upgradeName) as upgrade:
                cliArguments = _parse_args(upgradeName)
                if cliArguments.runAgain or upgrade.isNeeded():
                    upgrade.log.debug("Running upgrade %s", upgradeName)
                    try:
                        f(*args, **kwargs)
                    except Exception:
                        upgrade.log.exception("Failed to run %s",
                                              upgradeName)
                        return 1
                    else:
                        upgrade.seal()

            return 0
        return wrapper
    return decorator
