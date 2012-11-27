#
# Copyright 2011-2012 Red Hat, Inc.
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

import threading
import time
import os

from vdsm import constants
from vdsm import utils
from vdsm.config import config


class KsmMonitorThread(threading.Thread):
    def __init__(self, cif):
        threading.Thread.__init__(self, name='KsmMonitor')
        self.setDaemon(True)
        self._cif = cif
        self.state, self.pages = False, 0
        self._lock = threading.Lock()
        if config.getboolean('ksm', 'ksm_monitor_thread'):
            pids = utils.execCmd([constants.EXT_PGREP, '-xf', 'ksmd'],
                                 raw=False, sudo=False)[1]
            if pids:
                self._pid = pids[0].strip()
                self._cif.log.info(
                    'starting ksm monitor thread, ksm pid is %s', self._pid)
                self.start()
            else:
                self._cif.log.error('failed to find ksmd thread')
        else:
            self._cif.log.info('ksm monitor thread disabled, not starting')
        self.cpuUsage = 0

    def _getKsmdJiffies(self):
        return sum(map(int, file('/proc/%s/stat' % self._pid)
                                    .read().split()[13:15]))

    def run(self):
        start()
        try:
            KSM_MONITOR_INTERVAL = 60
            jiff0 = self._getKsmdJiffies()
            while True:
                time.sleep(KSM_MONITOR_INTERVAL)
                jiff1 = self._getKsmdJiffies()
                self.cpuUsage = (jiff1 - jiff0) % 2 ** 32 * 100 / \
                                os.sysconf('SC_CLK_TCK') / KSM_MONITOR_INTERVAL
                jiff0 = jiff1
        except:
            self._cif.log.error("Error monitoring KSM", exc_info=True)

    def adjust(self):
        """Adjust ksm's vigor

        Recalculate how hard should ksm work, according to configuration and
        current memory stress.
        Return whether ksm is running"""

        self._lock.acquire()
        try:
            utils.execCmd([constants.EXT_SERVICE, 'ksmtuned', 'retune'],
                          sudo=True)
        finally:
            self._lock.release()
        return running()

    def memsharing(self):
        try:
            return (int(file('/sys/kernel/mm/ksm/pages_sharing').read()))
        except:
            return 0


def running():
    try:
        return int(file('/sys/kernel/mm/ksm/run').read()) & 1 == 1
    except:
        return False


def npages():
    try:
        return int(file('/sys/kernel/mm/ksm/pages_to_scan').read())
    except:
        return 0


def start():
    utils.execCmd([constants.EXT_SERVICE, 'ksmtuned', 'start'], sudo=True)
    utils.execCmd([constants.EXT_SERVICE, 'ksm', 'start'], sudo=True)


def tune(params):
    # For supervdsm
    KSM_PARAMS = {'run': 3, 'sleep_millisecs': 0x100000000,
                  'pages_to_scan': 0x100000000}
    for (k, v) in params.iteritems():
        if k not in KSM_PARAMS.iterkeys():
            raise Exception('Invalid key in KSM parameter: %s=%s' % (k, v))
        if int(v) < 0 or int(v) >= KSM_PARAMS[k]:
            raise Exception('Invalid value in KSM parameter: %s=%s' % (k, v))
        with open('/sys/kernel/mm/ksm/%s' % k, 'w') as f:
            f.write(str(v))
