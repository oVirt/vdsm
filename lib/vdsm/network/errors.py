# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division


ERR_BAD_PARAMS = 21
ERR_BAD_ADDR = 22
ERR_BAD_NIC = 23
ERR_USED_NIC = 24
ERR_BAD_BONDING = 25
ERR_BAD_VLAN = 26
ERR_BAD_BRIDGE = 27
ERR_FAILED_IFUP = 29
ERR_USED_BOND = 31
ERR_LOST_CONNECTION = 10  # noConPeer
ERR_OVS_CONNECTION = 32


class ConfigNetworkError(Exception):
    def __init__(self, errCode, message):
        self.errCode = errCode
        self.msg = message
        super(ConfigNetworkError, self).__init__(errCode, message)


class RollbackIncomplete(Exception):
    """
    This exception is raised in order to signal vdsm.API.Global that a call to
    setupNetworks has failed and there are leftovers that need to be cleaned
    up.
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
        if isinstance(arg, str):
            return arg
