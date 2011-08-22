#
# Copyright 2011 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import logging
import sys

class SimpleLogAdapter(logging.LoggerAdapter):
    # Because of how python implements the fact that warning
    # and warn are the same. I need to reimplement it here. :(
    warn = logging.LoggerAdapter.warning

    def process(self, msg, kwargs):
        result = ''
        for key, value in self.extra.iteritems():
            result += '%s=`%s`' % (key, value)
        result += '::%s' % msg
        return (result, kwargs)

class TracebackRepeatFilter(logging.Filter):
    """
    Makes sure a traceback is logged only once for each exception.
    """
    def filter(self, record):
        if not record.exc_info:
            return 1

        info = sys.exc_info()
        ex = info[1]
        if ex is None:
            return 1

        if hasattr(ex, "_logged") and ex._logged:
            record.exc_info = False
            ex._logged = True

        return 1

