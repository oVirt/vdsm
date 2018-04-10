#
# Copyright 2017 Red Hat, Inc.
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

"""
errors - vdsm internal errors

This module provide internal errors which are not part of vdsm api, helpers for
error handling. For public vdsm errors see vdsm.common.exception.
"""

from __future__ import absolute_import
from __future__ import division


class Base(Exception):
    msg = "Base class for vdsm errors"

    def __str__(self):
        return self.msg.format(self=self)
