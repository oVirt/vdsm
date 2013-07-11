# Copyright 2013 Red Hat, Inc.
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
import sys

from vdsm import utils
import vdsm.tool
from vdsm.constants import P_VDSM_EXEC


def exec_libvirt_configure(action, *args):
    """
    Invoke libvirt_configure.sh script
    """
    if os.getuid() != 0:
        raise RuntimeError("Must run as root")

    rc, out, err = utils.execCmd([os.path.join(
                                  P_VDSM_EXEC,
                                  'libvirt_configure.sh'), action] +
                                 list(args),
                                 raw=True)

    sys.stdout.write(out)
    sys.stderr.write(err)

    if rc != 0:
        raise RuntimeError("Failed to configure libvirt")

    return 0


@vdsm.tool.expose("libvirt-configure")
def configure_libvirt(*args):
    """
    libvirt configuration (--force for reconfigure)
    """
    return exec_libvirt_configure("reconfigure",
                                  *args)


@vdsm.tool.expose("libvirt-test-conflicts")
def test_conflict_configurations(*args):
    """
    Validate conflict in configured files
    """
    return exec_libvirt_configure("test_conflict_configurations", *args)
