#
# Copyright (C) 2012, IBM Corporation
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

import logging
import socket

import six

from vdsm.common.define import Mbytes
from vdsm.config import config
from vdsm import throttledlog

from vdsm.common.cpuarch import PAGE_SIZE_BYTES
from vdsm.common import unixrpc


throttledlog.throttle('MomNotAvailable', 100)
throttledlog.throttle('MomNotAvailableKSM', 100)


class MomClient(object):

    def __init__(self, sock_path):
        self.log = logging.getLogger("MOM")
        self.log.info("Preparing MOM interface")
        self._sock_path = sock_path
        self._mom = None
        self._policy = {}

    def connect(self):
        if self._mom is not None:
            return

        self.log.info("MOM: Using named unix socket: %s",
                      self._sock_path)
        self._mom = unixrpc.UnixXmlRpcClient(self._sock_path)

    def getKsmStats(self):
        """
        Get information about KSM and convert memory data from page
        based values to MiB.
        """

        ret = {}

        try:
            stats = self._mom.getStatistics()['host']
            ret['ksmState'] = bool(stats['ksm_run'])
            ret['ksmPages'] = stats['ksm_pages_to_scan']
            ret['ksmMergeAcrossNodes'] = bool(stats['ksm_merge_across_nodes'])
            ret['memShared'] = stats['ksm_pages_sharing'] * PAGE_SIZE_BYTES
            ret['memShared'] //= Mbytes
            ret['ksmCpu'] = stats['ksmd_cpu_usage']
        except (AttributeError, socket.error):
            throttledlog.warning('MomNotAvailableKSM',
                                 "MOM not available, "
                                 "KSM stats will be missing.")

        return ret

    def setPolicy(self, policyStr):
        try:
            # mom.setPolicy will raise an exception on failure.
            self._mom.setPolicy(policyStr)
        except (AttributeError, socket.error):
            self.log.warning("MOM not available, Policy could not be set.")

    def setPolicyParameters(self, key_value_store):
        # mom.setNamedPolicy will raise an exception on failure.

        # Prepare in-memory policy file with tuning variables
        # this might need to convert certain python types to proper MoM
        # policy language
        self._policy.update(key_value_store)

        # Python bool values are defined in 00-defines.policy so need no
        # conversion here
        policy_string = "\n".join(["(set %s %r)" % (k, v)
                                   for k, v in six.iteritems(self._policy)])

        try:
            self._mom.setNamedPolicy(config.get("mom", "tuning_policy"),
                                     policy_string)
        except (AttributeError, socket.error):
            self.log.warning("MOM not available, Policy could not be set.")

    def getStatus(self):
        try:
            if self._mom.ping():
                return 'active'
            else:
                return 'inactive'
        except (AttributeError, socket.error):
            throttledlog.warning('MomNotAvailable', "MOM not available.")
            return 'inactive'
