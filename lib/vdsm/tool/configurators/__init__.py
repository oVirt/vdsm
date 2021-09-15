#
# Copyright 2014 Red Hat, Inc.
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
from vdsm.tool import UsageError


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
YES, NO, MAYBE = tuple(range(3))
