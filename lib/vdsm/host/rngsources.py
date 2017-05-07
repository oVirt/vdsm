#
# Copyright 2016-2017 Red Hat, Inc.
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

import os.path

from vdsm.common.cache import memoized

_SOURCES = {
    'random': '/dev/random',
    'urandom': '/dev/urandom',
    'hwrng': '/dev/hwrng'
}


def list_available():
    return [
        source for (source, path) in _SOURCES.items()
        if os.path.exists(path) and
        # REQUIRE_FOR: Engine <= 4.0
        source != 'urandom'
    ]


def get_device(name):
    return _SOURCES[name]


@memoized
def get_source_name(dev):
    for name, path in _SOURCES.items():
        if dev == path:
            return name
    raise KeyError(dev)
