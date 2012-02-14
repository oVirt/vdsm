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

import os
import pwd
import logging
import threading
import sys

def runVdsm(baseDir="/usr/share/vdsm/", configFilePath="/etc/vdsm/vdsm.conf", loggerConfigurationPath='/etc/vdsm/logger.conf'):
    """
    Starts a VDSM instance in a new thread and returns a tuple ``(ClientIF, Thread Running VDSM)``
    """
    if pwd.getpwuid(os.geteuid())[0] != "vdsm":
        raise Exception("You can't run vdsm with any user other then 'vdsm'.")

    sys.path.append(baseDir)

    from vdsm.config import config
    from logging import config as lconfig
    import clientIF

    loggerConfFile = loggerConfigurationPath
    lconfig.fileConfig(loggerConfFile)
    log = logging.getLogger('vds')

    config.read(configFilePath)

    cif = clientIF.clientIF(log)

    t = threading.Thread(target = cif.serve)
    t.setDaemon(True)
    t.start()

    return (cif, t)

