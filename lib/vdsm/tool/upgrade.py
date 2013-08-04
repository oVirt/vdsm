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

from functools import wraps
import logging
import logging.config
from logging.handlers import SysLogHandler
import os

from .. import constants
from ..utils import touchFile


LOGGER_CONF_FILE = constants.P_VDSM_CONF + 'logger.conf'


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
        self._upgradeFilePath = os.path.join(constants.P_VDSM_LIB,
                                             "upgrade", self._upgradeName)
        try:
            logging.config.fileConfig(LOGGER_CONF_FILE)
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
                if upgrade.isNeeded():
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
