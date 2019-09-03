#
# Copyright 2012-2016 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import six

from collections import defaultdict

from vdsm.common.exception import GeneralException
from vdsm.storage import exception as storage_exception


def test_collisions():
    codes = defaultdict(list)

    for name in dir(storage_exception):
        obj = getattr(storage_exception, name)

        if not isinstance(obj, type):
            continue

        if not issubclass(obj, GeneralException):
            continue

        codes[obj.code].append(name)

    problems = [(k, v) for k, v in six.iteritems(codes)
                if len(v) != 1 or k >= 5000]

    assert not problems, "Duplicated or invalid exception code"
