# Copyright (C) 2013, IBM Corporation
# Copyright (C) 2013-2014, Red Hat, Inc.
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

import os
import signal
import threading

from vdsm import netinfo
from vdsm.utils import CommandPath
from vdsm.utils import execCmd
from vdsm.utils import rmFile


class DhcpClient(object):
    PID_FILE = '/var/run/dhclient-%s.pid'
    LEASE_DIR = '/var/lib/dhclient/'
    LEASE_FILE = LEASE_DIR + 'dhclient-%s.lease'
    DHCLIENT = CommandPath('dhclient', '/sbin/dhclient')

    def __init__(self, iface):
        self.iface = iface
        self.pidFile = self.PID_FILE % self.iface
        if not os.path.exists(self.LEASE_DIR):
            os.mkdir(self.LEASE_DIR)
        self.leaseFile = (self.LEASE_FILE % self.iface)

    def _dhclient(self):
        # Ask dhclient to stop any dhclient running for the device
        if os.path.exists(os.path.join(netinfo.NET_PATH, self.iface)):
            kill_dhclient(self.iface)
        rc, out, err = execCmd([self.DHCLIENT.cmd, '-1', '-pf',
                                self.pidFile, '-lf', self.leaseFile,
                                self.iface])
        return rc, out, err

    def start(self, async):
        if async:
            t = threading.Thread(target=self._dhclient, name='vdsm-dhclient-%s'
                                 % self.iface)
            t.daemon = True
            t.start()
        else:
            rc, out, err = self._dhclient()
            return rc

    def shutdown(self):
        try:
            pid = int(open(self.pidFile).readline().strip())
        except IOError as e:
            if e.errno == os.errno.ENOENT:
                pass
            else:
                raise
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as e:
                if e.errno == os.errno.ESRCH:
                    pass
                else:
                    raise
            rmFile(self.pidFile)


def kill_dhclient(device_name):
    execCmd([DhcpClient.DHCLIENT.cmd, '-x', device_name])
