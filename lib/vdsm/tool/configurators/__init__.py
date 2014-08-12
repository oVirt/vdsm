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
    """ raise when the environemnt is not valid to run the command """
    pass

# Declare state of configuration
#
# CONFIGURED     = Module is set properly without any required changes on
#                  force.
# NOT_CONFIGURED = Module is not set properly for VDSM and need to be
#                  configured.
# NOT_SURE       = VDSM configured module already but on force configure vdsm
#                  will set configurations to defaults parameters.
#
CONFIGURED, NOT_CONFIGURED, NOT_SURE = range(3)


class ModuleConfigure(object):

    def __init__(self):
        pass

    def getName(self):
        return None

    def getServices(self):
        return []

    def validate(self):
        return True

    def configure(self):
        pass

    def isconfigured(self):
        return NOT_CONFIGURED

    def removeConf(self):
        pass

    def getRequires(self):
        return set()
