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

import six
from vdsm import compat

try:
    import statsd
except ImportError as e:
    raise compat.Unsupported(str(e))

_client = None


def start(address):
    global _client
    if _client is None:
        _client = statsd.StatsClient(host=address)


def stop():
    # client doesn't support any close mechanism
    pass


def send(report):
    for name, value in six.iteritems(report):
        _client.gauge(name, value)
