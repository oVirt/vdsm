#
# Copyright 2014 Hat, Inc.
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

from .. import UsageError


class InvalidConfig(UsageError):
    """ raise when invalid configuration passed """
    pass


class InvalidRun(UsageError):
    """ raise when the environment is not valid to run the command """
    pass

# Declare state of configuration
#
# YES   = Module configured.
#
# NO    = Module not configured before.
#
# MAYBE = Module configured before,
#         configuration validity could not be determined.
#
# See also --force at configurators.py.
YES, NO, MAYBE = range(3)


class ModuleConfigure(object):
    """A ModuleConfigure handles an aspect of vdsm's configuration life cycle.

    Including:
    - Configure the machine to run vdsm after package installation.
    - Cleanup configuration before package removal.
    - Check configuration status and validity during init.
    """
    @property
    def name(self):
        """Return module name to be used with the --module option.

        Must be overridden by subclass.
        """
        raise NotImplementedError()

    @property
    def requires(self):
        """Return a set of other modules names required by this module.

        Those modules will be included even if not provided in --module.

        May be overridden by subclass.
        """
        return frozenset()

    @property
    def services(self):
        """return the names of services to reload.

        These services will be stopped before this configurator is called,
        and will be started in reversed order when the configurator is done.

        May be overridden by subclass.
        """
        return ()

    def validate(self):
        """Return True if this module's configuration is valid.

        Note: Returning False will cause vdsm to abort during initialization.

        May be overridden by subclass.
        """
        return True

    def configure(self):
        """Prepare this machine to run vdsm.

        May be overridden by subclass.
        """
        pass

    def isconfigured(self):
        """Return the configuration status of this module.

        see possible values above.

        Note: returning NO will cause vdsm to abort during
        initialization.

        Note: after configure isconfigured should return MAYBE or
        YES.

        May be overridden by subclass.
        """
        return NO

    def removeConf(self):
        """Cleanup vdsm's configuration.

        May be overridden by subclass.
        """
        pass
