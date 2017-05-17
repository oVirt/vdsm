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
This module is left behind as a proxy to the relocated logutils module.
The two proxy functions have been referenced in logger.conf, therefore,
any user that may have customized the logger.conf, would not get the updated
version with the new references.

TODO: The configuration in these specific entries in the logger.config.in
should be drop, letting the application define them (without exposing this
to users).
"""
from __future__ import absolute_import

from vdsm.common.logutils import TimezoneFormatter  # NOQA: F401 (unused import)
from vdsm.common.logutils import UserGroupEnforcingHandler  # NOQA: F401 (unused import)
