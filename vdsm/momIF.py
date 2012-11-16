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
try:
    import mom
    _momAvailable = True
except ImportError:
    _momAvailable = False


class MomThread(threading.Thread):

    def __init__(self, momconf):
        if not _momAvailable:
            raise Exception("MOM is not available")
        self.log = logging.getLogger("MOM")
        self.log.info("Starting up MOM")
        self._mom = mom.MOM(momconf)
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

    def stop(self):
        if self._mom is not None:
            self.log.info("Shutting down MOM")
            self._mom.shutdown()

    def getStatus(self):
        if self.isAlive():
            return 'active'
        else:
            return 'inactive'
