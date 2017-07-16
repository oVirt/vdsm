# Copyright 2014 Red Hat, Inc.
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
import os
import selinux
import shutil
import sys
import tempfile
import time

from . import YES, NO
from vdsm.tool import service
from vdsm import commands
from vdsm import constants


_CONF_FILE = "/etc/multipath.conf"

# The first line of multipath.conf configured by vdsm must contain a
# "VDSM REVISION X.Y" tag.  Note that older version used "RHEV REVISION X.Y"
# format.

_CURRENT_TAG = "# VDSM REVISION 1.3"

_OLD_TAGS = (
    "# VDSM REVISION 1.2",
    "# RHEV REVISION 1.1",
    "# RHEV REVISION 1.0",
    "# RHEV REVISION 0.9",
    "# RHEV REVISION 0.8",
    "# RHEV REVISION 0.7",
    "# RHEV REVISION 0.6",
    "# RHEV REVISION 0.5",
    "# RHEV REVISION 0.4",
    "# RHEV REVISION 0.3",
    "# RHAT REVISION 0.2",
)

# The second line of multipath.conf may contain PRIVATE_TAG. This means
# vdsm-tool should never change the conf file even when using the --force flag.

_PRIVATE_TAG = "# VDSM PRIVATE"
_OLD_PRIVATE_TAG = "# RHEV PRIVATE"

_CONF_DATA = """\
%(current_tag)s

defaults {
    polling_interval            5
    no_path_retry               fail
    user_friendly_names         no
    flush_on_last_del           yes
    fast_io_fail_tmo            5
    dev_loss_tmo                30
    max_fds                     4096
}

# Remove devices entries when overrides section is available.
devices {
    device {
        # These settings overrides built-in devices settings. It does not apply
        # to devices without built-in settings (these use the settings in the
        # "defaults" section), or to devices defined in the "devices" section.
        # Note: This is not available yet on Fedora 21. For more info see
        # https://bugzilla.redhat.com/1253799
        all_devs                yes
        no_path_retry           fail
    }
}

# Enable when this section is available on all supported platforms.
# Options defined here override device specific options embedded into
# multipathd.
#
# overrides {
#      no_path_retry           fail
# }

""" % {"current_tag": _CURRENT_TAG}

# If multipathd is up, it will be reloaded after configuration,
# or started before vdsm starts, so service should not be stopped
# during configuration.
services = []


def configure():
    """
    Set up the multipath daemon configuration to the known and
    supported state. The original configuration, if any, is saved
    """

    if os.path.exists(_CONF_FILE):
        backup = _CONF_FILE + '.' + time.strftime("%Y%m%d%H%M")
        shutil.copyfile(_CONF_FILE, backup)
        sys.stdout.write("Backup previous multipath.conf to %r\n" % backup)

    with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=os.path.basename(_CONF_FILE) + ".tmp",
            dir=os.path.dirname(_CONF_FILE),
            delete=False) as f:
        try:
            f.write(_CONF_DATA)
            f.flush()
            selinux.restorecon(f.name)
            os.chmod(f.name, 0o644)
            os.rename(f.name, _CONF_FILE)
        except:
            os.unlink(f.name)
            raise

    # Flush all unused multipath device maps
    commands.execCmd([constants.EXT_MULTIPATH, "-F"])

    try:
        service.service_reload("multipathd")
    except service.ServiceOperationError:
        status = service.service_status("multipathd", False)
        if status == 0:
            raise


def isconfigured():
    """
    Check the multipath daemon configuration. The configuration file
    /etc/multipath.conf should contain a tag in form
    "RHEV REVISION X.Y" for this check to succeed.
    If the tag above is followed by tag "RHEV PRIVATE" the configuration
    should be preserved at all cost.
    """

    if os.path.exists(_CONF_FILE):
        first = second = ''
        with open(_CONF_FILE) as f:
            mpathconf = [x.strip("\n") for x in f.readlines()]
        try:
            first = mpathconf[0]
            second = mpathconf[1]
        except IndexError:
            pass
        if _PRIVATE_TAG in second or _OLD_PRIVATE_TAG in second:
            sys.stdout.write("Manual override for multipath.conf detected"
                             " - preserving current configuration\n")
            if _CURRENT_TAG not in first:
                sys.stdout.write("This manual override for multipath.conf "
                                 "was based on downrevved template. "
                                 "You are strongly advised to "
                                 "contact your support representatives\n")
            return YES

        if _CURRENT_TAG in first:
            sys.stdout.write("Current revision of multipath.conf detected,"
                             " preserving\n")
            return YES

        for tag in _OLD_TAGS:
            if tag in first:
                sys.stdout.write("Downrev multipath.conf detected, "
                                 "upgrade required\n")
                return NO

    sys.stdout.write("multipath requires configuration\n")
    return NO
