#
# Copyright 2011-2014 Red Hat, Inc.
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
from __future__ import division

import six

ERR_OK = 0
ERR_BAD_PARAMS = 21
ERR_BAD_ADDR = 22
ERR_BAD_NIC = 23
ERR_USED_NIC = 24
ERR_BAD_BONDING = 25
ERR_BAD_VLAN = 26
ERR_BAD_BRIDGE = 27
ERR_USED_BRIDGE = 28
ERR_FAILED_IFUP = 29
ERR_FAILED_IFDOWN = 30
ERR_USED_BOND = 31
ERR_LOST_CONNECTION = 10  # noConPeer
ERR_OVS_CONNECTION = 32


class ConfigNetworkError(Exception):
    def __init__(self, errCode, message):
        self.errCode = errCode
        self.msg = message
        super(ConfigNetworkError, self).__init__(errCode, message)


class OvsDBConnectionError(ConfigNetworkError):
    def __init__(self, *args):
        message = _get_message(args)
        super(OvsDBConnectionError, self).__init__(
            errCode=ERR_OVS_CONNECTION, message=message
        )

    @staticmethod
    def is_ovs_db_conn_error(err_msg):
        return 'database connection failed' in err_msg[0]


class RollbackIncomplete(Exception):
    """
    This exception is raised in order to signal vdsm.API.Global that a call to
    setupNetworks has failed and there are leftovers that need to be cleaned
    up.
    Note that it is never raised by the default ifcfg configurator.
    """

    def __init__(self, diff, exc_type, value):
        self.diff = diff
        self.exc_type = exc_type
        self.value = value
        super(RollbackIncomplete, self).__init__()

    def __str__(self):
        return '{} : diff={} exc_type={} value={}'.format(
            self.__class__.__name__, self.diff, self.exc_type, self.value
        )


def _get_message(args):
    """
    Due to multiprocessing limitation in the way it processes an exception
    serialization and deserialization, a derived exception needs to accept
    all super classes arguments as input, even if it ignores them.

    Given the list of arguments and assuming the message is a string type,
    this helper function fetches the message argument.
    """
    for arg in args:
        if isinstance(arg, six.string_types):
            return arg
