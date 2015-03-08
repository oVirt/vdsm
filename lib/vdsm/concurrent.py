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

import logging
import threading

log = logging.getLogger("vds.concurrent")


def tmap(func, iterable):
    resultsDict = {}
    error = [None]

    def wrapper(f, arg, index):
        try:
            resultsDict[index] = f(arg)
        except Exception as e:
            # We will throw the last error received
            # we can only throw one error, and the
            # last one is as good as any. This shouldn't
            # happen. Wrapped methods should not throw
            # exceptions, if this happens it's a bug
            log.error("tmap caught an unexpected error", exc_info=True)
            error[0] = e
            resultsDict[index] = None

    threads = []
    for i, arg in enumerate(iterable):
        t = threading.Thread(target=wrapper, args=(func, arg, i))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    results = [None] * len(resultsDict)
    for i, result in resultsDict.iteritems():
        results[i] = result

    if error[0] is not None:
        raise error[0]

    return tuple(results)
