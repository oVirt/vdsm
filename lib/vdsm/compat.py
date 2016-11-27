#
# Copyright 2014-2017 Red Hat, Inc.
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

import six


class Unsupported(ImportError):
    """
    Raised when a feature is not supported on this platform.
    """


class MissingModule(object):
    """
    Placeholder for missing module.

    Can be used when a 3rd party module is not available on this platform, but
    the code using the module can still partly work, or be tested on the
    platform without the module.  Any operation on the module will raise
    Unsupported exception.

    Example usage::

        try:
            import foobar
        except ImportError:
            if six.PY2:
                raise
            # foobar is not available yet on python 3 but we can still
            # test this code using fakefoobar.
            foobar = compat.MissingModule("foobar is missing")

    This will raise compat.Unsupported::

        foobar.do_something()
    """

    def __init__(self, message):
        self._message = message

    def __getattr__(self, name):
        raise Unsupported(self._message)


try:
    import cPickle as pickle
    pickle  # make pyflakes happy
except ImportError:  # py3
    import pickle
    pickle  # yep, this is needed twice.

try:
    # on RHEL/Centos 6.x, the JSON module in the python standard
    # library does not include significant speedups:
    # stdlib is based on simplejson 1.9, speedups were added on 2.0.9.
    # In general, speedups are first found on the
    # simplejson package.
    import simplejson as json
    json  # make pyflakes happy
except ImportError:
    # no big deal, fallback to standard library
    import json
    json  # yep, this is needed twice.

if six.PY2:
    from cpopen import CPopen
    CPopen  # make pyflakes happy
else:
    from subprocess import Popen as CPopen
    CPopen  # make pyflakes happy

try:
    from contextlib import suppress
    suppress  # make pyflakes happy
except ImportError:
    from vdsm.common.contextlib import suppress
    suppress  # yep, this is needed twice.

try:
    from glob import escape as glob_escape
    glob_escape  # make pyflakes happy
except ImportError:
    from vdsm.common.glob import escape as glob_escape
    glob_escape  # make pyflakes happy
