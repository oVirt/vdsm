#
# Copyright 2015 Red Hat, Inc.
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
import threading
from collections import namedtuple


Result = namedtuple("Result", ["succeeded", "value"])


def tmap(func, iterable):
    args = list(iterable)
    results = [None] * len(args)

    def worker(i, f, arg):
        try:
            results[i] = Result(True, f(arg))
        except Exception as e:
            results[i] = Result(False, e)

    threads = []
    for i, arg in enumerate(args):
        t = threading.Thread(target=worker, args=(i, func, arg))
        t.daemon = True
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return results
