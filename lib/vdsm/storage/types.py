#
# Copyright 2016 Red Hat, Inc.
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
types - vdsm storage types

This module include the storage types mentioned in vdsm schema.  The purpose of
these types is to validate the input and provide an easy way to pass the
arguments around.
"""

from __future__ import absolute_import

from vdsm import properties


class Lease(properties.Owner):
    """
    External sanlock lease.
    """
    sd_id = properties.UUID(required=True)
    lease_id = properties.UUID(required=True)

    def __init__(self, params):
        self.sd_id = params.get("sd_id")
        self.lease_id = params.get("lease_id")
