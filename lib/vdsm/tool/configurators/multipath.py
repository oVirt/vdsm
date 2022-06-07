# Copyright 2014-2017 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division
import os
import sys

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.storage import mpathconf
from vdsm.tool import service

from . import YES, NO

_MULTIPATHD = cmdutils.CommandPath("multipathd", "/usr/sbin/multipathd")


# If multipathd is up, it will be reloaded after configuration,
# or started before vdsm starts, so service should not be stopped
# during configuration.
services = []


def configure():
    """
    Set up the multipath daemon configuration and start the service.
    """
    backup = mpathconf.configure_multipathd()
    if backup:
        sys.stdout.write(f"Previous multipath.conf copied to {backup}\n")

    # We want to handle these cases:
    #
    # 1. multipathd is not running and the kernel module is not loaded. We
    #    don't have any online multipath devices, so there is nothing to
    #    reconfigure, but we want to make sure that multipathd is happy with
    #    new multipath configuration.
    #
    # 2. multipathd is not running but the kernel module is loaded. We may have
    #    online devices that need reconfiguration.
    #
    # 3. multipathd is running with older configuration not compatible with
    #    vdsm configuration.
    #
    # 4. multipathd is running with older vdsm configuration. Reloading is
    #    needed to update devices with new configuration.
    #
    # When we have online devices using incompatible configuration, they may
    # use "user friendly" names (/dev/mapper/mpath{N}) instead of consistent
    # names (/dev/mapper/{WWID}). Restarting multipathd with the new
    # configuration will rename the devices, however due to multipathd bug,
    # these devices may not pick all configuration changes. Reconfiguring
    # multipathd ensures that all configurations changes are applied.
    #
    # To simplify handing of all cases, we first start multipathd service. This
    # eliminates cases 1 and 2. Case 3 and 4 are handled by reconfiguring
    # multipathd.
    #
    # If any of those steps fails, we want to fail the configuration.

    service.service_start("multipathd")
    commands.run([_MULTIPATHD.cmd, "reconfigure"])


def isconfigured():
    """
    Check the multipath daemon configuration.
    """

    if os.path.exists(mpathconf.CONF_FILE):
        revision, private = mpathconf.read_metadata()
        if private:
            sys.stdout.write("Manual override for multipath.conf detected"
                             " - preserving current configuration\n")
            if revision != mpathconf.REVISION_OK:
                sys.stdout.write("This manual override for multipath.conf "
                                 "was based on downrevved template. "
                                 "You are strongly advised to "
                                 "contact your support representatives\n")
            return YES

        if revision == mpathconf.REVISION_OK:
            sys.stdout.write("Current revision of multipath.conf detected,"
                             " preserving\n")
            return YES

        if revision == mpathconf.REVISION_OLD:
            sys.stdout.write("Downrev multipath.conf detected, "
                             "upgrade required\n")
            return NO

        if revision == mpathconf.REVISION_MISSING:
            sys.stdout.write("No revision of multipath.conf detected.\n")
            return NO

    sys.stdout.write("multipath requires configuration\n")
    return NO
