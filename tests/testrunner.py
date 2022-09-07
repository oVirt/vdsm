# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import testlib
import logging
import os
import sys

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
