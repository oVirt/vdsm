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
from __future__ import absolute_import

from functools import wraps
import logging

from . import exception
from . import response


_log = logging.getLogger("virt.api")


def method(func):
    """
    Decorate an instance method, and return a response according to the
    outcome of the call.

    If the method returns None, return a plain success response.
    If the method wants to augment the success response, it could return
    a dict. The dict items will be added to the success response.
    The method could override the success response message this way.

    If the method raises a VdsmException or one subclass, the decorator
    will produce the corresponding error response.
    If the method raises any other exception, the decorator will produce a
    general exception response with the details of the original error.
    """

    @wraps(func)
    def wrapper(self, *args, **kwargs):

        _log.debug("START %s args=%s kwargs=%s", func.__name__, args, kwargs)
        try:
            ret = func(self, *args, **kwargs)
        except Exception as e:
            _log.exception("FINISH %s error=%s", func.__name__, e)
            if not isinstance(e, exception.VdsmException):
                e = exception.GeneralException(str(e))
            return e.response()

        _log.debug("FINISH %s response=%s", func.__name__, ret)

        if ret is None:
            return response.success()
        else:
            return response.success(**ret)

    return wrapper
