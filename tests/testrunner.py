#
# Copyright 2012-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import testlib
import logging
import os
import sys

from vdsm.common import zombiereaper
zombiereaper.registerSignalHandler()

TEST_LOG = '/var/log/vdsm_tests.log'


def findRemove(listR, value):
    """used to test if a value exist, if it is, return true and remove it."""
    try:
        listR.remove(value)
        return True
    except ValueError:
        return False


def configureLogging():
    if os.getuid() == 0:  # only root can create a logfile
        logging.basicConfig(
            filename=TEST_LOG,
            filemode='a',
            format='%(asctime)s,%(msecs)03d %(levelname)-7s (%(threadName)s) '
                   '[%(name)s] %(message)s (%(module)s:%(lineno)d)',
            datefmt='%H:%M:%S',
            level=logging.DEBUG)


if __name__ == '__main__':
    if "--help" in sys.argv:
        print("testrunner options:\n"
              "--local-modules   use vdsm modules from source tree, "
              "instead of installed ones.\n")
    if findRemove(sys.argv, "--local-modules"):
        from vdsm import constants
        from vdsm.common import constants as common_constants
        common_constants.P_VDSM = constants.P_VDSM = "../vdsm/"

    configureLogging()
    testlib.run()
