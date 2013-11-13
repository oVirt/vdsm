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

import logging
import threading
from vdsm.config import config

try:
    import mom
    _momAvailable = True
except ImportError:
    _momAvailable = False


class MomNotAvailableError(RuntimeError):
    pass


def isMomAvailable():
    return _momAvailable


class MomThread(threading.Thread):

    def __init__(self, momconf):
        if not _momAvailable:
            raise MomNotAvailableError()

        self.log = logging.getLogger("MOM")
        self.log.info("Starting up MOM")
        self._mom = mom.MOM(momconf)
        self._policy = {}
        threading.Thread.__init__(self, target=self._mom.run, name="MOM")
        self.start()

    def getKsmStats(self):
        stats = self._mom.getStatistics()['host']
        ret = {}
        ret['ksmState'] = bool(stats['ksm_run'])
        ret['ksmPages'] = stats['ksm_pages_to_scan']
        ret['memShared'] = stats['ksm_pages_sharing']
        ret['ksmCpu'] = stats['ksmd_cpu_usage']
        return ret

    def setPolicy(self, policyStr):
        # mom.setPolicy will raise an exception on failure.
        self._mom.setPolicy(policyStr)

    def setPolicyParameters(self, key_value_store):
        # mom.setNamedPolicy will raise an exception on failure.

        # Prepare in-memory policy file with tuning variables
        # this might need to convert certain python types to proper MoM
        # policy language
        self._policy.update(key_value_store)

        # Python bool values are defined in 00-defines.policy so need no
        # conversion here
        policy_string = "\n".join(["(set %s %r)" % (k, v)
                                   for k, v in self._policy.iteritems()])

        self._mom.setNamedPolicy(config.get("mom", "tuning_policy"),
                                 policy_string)

    def stop(self):
        if self._mom is not None:
            self.log.info("Shutting down MOM")
            self._mom.shutdown()

    def getStatus(self):
        if self.isAlive():
            return 'active'
        else:
            return 'inactive'
