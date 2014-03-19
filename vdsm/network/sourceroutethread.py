# Copyright 2013-2014 Red Hat, Inc.
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
import os
import threading

import pyinotify

from vdsm.constants import P_VDSM_RUN
from vdsm import utils

from .configurators.iproute2 import Iproute2
from .sourceroute import DynamicSourceRoute


SOURCE_ROUTES_FOLDER = P_VDSM_RUN + 'sourceRoutes'
configurator = Iproute2()


class DHClientEventHandler(pyinotify.ProcessEvent):
    def process_IN_CLOSE_WRITE_filePath(self, sourceRouteFilePath):
        logging.debug("Responding to DHCP response in %s" %
                      sourceRouteFilePath)
        with open(sourceRouteFilePath, 'r') as sourceRouteFile:
            sourceRouteContents = sourceRouteFile.read().split()
            action = sourceRouteContents[0]
            device = sourceRouteContents[-1]
            sourceRoute = DynamicSourceRoute(device, configurator)

            if sourceRoute.isVDSMInterface():
                if action == 'configure':
                    ip = sourceRouteContents[1]
                    mask = sourceRouteContents[2]
                    gateway = sourceRouteContents[3]
                    sourceRoute.configure(ip, mask, gateway)
                else:
                    sourceRoute.remove()
            else:
                logging.info("interface %s is not a libvirt interface" %
                             sourceRoute.device)

            DynamicSourceRoute.removeInterfaceTracking(device)

        os.remove(sourceRouteFilePath)

    def process_IN_CLOSE_WRITE(self, event):
        self.process_IN_CLOSE_WRITE_filePath(event.pathname)


def start():
    thread = threading.Thread(target=_subscribeToInotifyLoop,
                              name='sourceRoute')
    thread.daemon = True
    thread.start()


@utils.traceback()
def _subscribeToInotifyLoop():
    logging.debug("sourceRouteThread.subscribeToInotifyLoop started")

    # Subscribe to pyinotify event
    watchManager = pyinotify.WatchManager()
    handler = DHClientEventHandler()
    notifier = pyinotify.Notifier(watchManager, handler)
    watchManager.add_watch(SOURCE_ROUTES_FOLDER, pyinotify.IN_CLOSE_WRITE)

    # Run once manually in case dhclient operated while supervdsm was down
    # Run sorted so that if multiple files exist for an interface, we'll
    # execute them alphabetically and thus according to their time stamp
    for filePath in sorted(os.listdir(SOURCE_ROUTES_FOLDER)):
        handler.process_IN_CLOSE_WRITE_filePath(
            SOURCE_ROUTES_FOLDER + '/' + filePath)

    notifier.loop()
