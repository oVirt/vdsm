#
# Copyright 2012-2016 Red Hat, Inc.
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

import sys

# When using Python 2, we must monkey patch threading module before importing
# any other module.
if sys.version_info[0] == 2:
    import pthreading
    pthreading.monkey_patch()


from vdsm.common import zombiereaper
zombiereaper.registerSignalHandler()

import testlib


def findRemove(listR, value):
    """used to test if a value exist, if it is, return true and remove it."""
    try:
        listR.remove(value)
        return True
    except ValueError:
        return False


if __name__ == '__main__':
    if "--help" in sys.argv:
        print("testrunner options:\n"
              "--local-modules   use vdsm modules from source tree, "
              "instead of installed ones.\n")
    if findRemove(sys.argv, "--local-modules"):
        from vdsm import constants
        constants.P_VDSM = "../vdsm/"

    testlib.run()
