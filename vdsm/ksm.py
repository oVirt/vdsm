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

import threading, traceback, time
import os
import constants
import utils
from config import config

class KsmMonitorThread(threading.Thread):
    def __init__(self, cif):
        threading.Thread.__init__(self, name = 'KsmMonitor')
        self.setDaemon(True)
        self._cif = cif
        self.state, self.pages = False, 0
        self._lock = threading.Lock()
        if config.getboolean('ksm', 'ksm_monitor_thread'):
            pids = utils.execCmd([constants.EXT_PGREP, '-xf', 'ksmd'],
                                 raw=False, sudo=False)[1]
            if pids:
                self._pid = pids[0].strip()
                self.start()
            else:
                self._cif.log.error('failed to find ksmd thread')
        self.cpuUsage = 0

    def _getKsmdJiffies(self):
        return sum(map(int, file('/proc/%s/stat' % self._pid) \
                                    .read().split()[13:15]))

    def run(self):
        try:
            self.state, self.pages = self.readState()
            KSM_MONITOR_INTERVAL = 60
            jiff0 = self._getKsmdJiffies()
            while True:
                time.sleep(KSM_MONITOR_INTERVAL)
                jiff1 = self._getKsmdJiffies()
                self.cpuUsage = (jiff1 - jiff0) % 2**32 * 100 / \
                                os.sysconf('SC_CLK_TCK') / KSM_MONITOR_INTERVAL
                jiff0 = jiff1
        except:
            self._cif.log.error(traceback.format_exc())

    def readState(self):
        return running(), npages()

    def adjust(self):
        """adjust ksm state according to configuration and current memory stress
        return whether ksm is running"""

        self._lock.acquire()
        try:
            utils.execCmd([constants.EXT_SERVICE, 'ksmtuned', 'retune'], sudo=True)
            self.state, self.pages = self.readState()
        finally:
            self._lock.release()
        return self.state

def running():
    try:
        state = int(file('/sys/kernel/mm/ksm/run').read()) & 1 == 1
        return state
    except:
        return False

def npages():
    try:
        npages = int(file('/sys/kernel/mm/ksm/pages_to_scan').read())
        return npages
    except:
        return 0

def start():
    if not running():
        utils.execCmd([constants.EXT_SERVICE, 'ksmtuned', 'start'], sudo=True)
        utils.execCmd([constants.EXT_SERVICE, 'ksm', 'start'], sudo=True)

def stop():
    if running():
        utils.execCmd([constants.EXT_SERVICE, 'ksmtuned', 'stop'], sudo=True)
        utils.execCmd([constants.EXT_SERVICE, 'ksm', 'stop'], sudo=True)
