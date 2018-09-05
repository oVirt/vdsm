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
import os
import selinux
import shutil
import sys
import tempfile
import time

from . import YES, NO
from vdsm.tool import service
from vdsm.common import commands
from vdsm import constants


_CONF_FILE = "/etc/multipath.conf"

# The first line of multipath.conf configured by vdsm must contain a
# "VDSM REVISION X.Y" tag.  Note that older version used "RHEV REVISION X.Y"
# format.

_CURRENT_TAG = "# VDSM REVISION 1.7"

_OLD_TAGS = (
    "# VDSM REVISION 1.6",
    "# VDSM REVISION 1.5",
    "# VDSM REVISION 1.4",
    "# VDSM REVISION 1.3",
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

# Once multipathd notices that the last path has failed, it will check
# all paths "no_path_retry" more times. If no paths are up, it will tell
# the kernel to stop queuing.  After that, all outstanding and future
# I/O will immediately be failed, until a path is restored. Once a path
# is restored the delay is reset for the next time all paths fail.
_NO_PATH_RETRY = 4

_CONF_DATA = """\
%(current_tag)s

# This file is managed by vdsm.
#
# The recommended way to add configuration for your storage is to add a
# drop-in configuration file in "/etc/multipath/conf.d/<mydevice>.conf".
# Settings in drop-in configuration files override settings in this
# file.
#
# If you want to modify this file, you must make it private so vdsm will
# never modify it. To make the file private, set the second line of this
# file to "# VDSM PRIVATE".

defaults {

    # Ensures fast detection of path failures.
    #
    # Interval between two path checks in seconds. If polling interval
    # is longer, it will take more time to discover that a path failed
    # or was reinstated.

    polling_interval            5

    # Ensures fast fail when no path is available.
    #
    # The number of retries until disable queueing, or fail for
    # immediate failure (no queueing).
    #
    # oVirt is not designed to handle unlimited queuing used by many
    # devices (no_path_retry queue) or big number of retries
    # (no_path_retry 60).  These settings cause commands run by vdsm
    # (e.g. lvm) to get stuck for a long time, causing timeouts in
    # various flows, and may cause a host to become non-responsive.
    #
    # We use a small number of retries to protect from short outage.
    # Assuming the default polling_interval (5 seconds), this gives
    # extra 20 seconds grace time before failing the I/O.

    no_path_retry               %(no_path_retry)d

    # Required for having same device names on all hosts.
    # DO NOT CHANGE!

    user_friendly_names         no

    # Helps propagating I/O errors to processes when the last path
    # failed.  Not necessary when using "no_path_retry fail".

    flush_on_last_del           yes

    # Ensures fast failover between paths.
    #
    # the  number  of seconds the scsi layer will wait after a problem
    # has been detected before failing IO to devices.
    #
    # With iSCSI, multipath uses this value to change the iSCSI session
    # timeout, which is 120 seconds by default.
    #
    # With FC, multipath sets the sysfs fast_io_fail_tmo value for the
    # remote ports associated with the path devices.  Multipath sets
    # this value whenever a device is reloaded (e.g. when a new path is
    # added or removed). This will override any change made in
    # /sys/class/fc_remote_ports/rport-*/fast_io_fail_tmo.
    #
    # This setting is not applicable to SAS devices.

    fast_io_fail_tmo            5

    # The number of seconds the scsi layer will wait after a problem has
    # been detected before removing a device from the system.
    #
    # For FC this is setting the sysfs dev_loss_tmo value for
    # the remote ports associated with the path devices.
    #
    # For SAS devices it is setting the I_T_nexus_loss_timeout value.
    #
    # There is no equivalent settable parameter for iSCSI devices so
    # this parameter is only applicable to FC and SAS devices.
    #
    # Usage:
    #
    # - Set this if you want failed devices to be removed from the system.
    # - Do not set this is you want failed devices to stay around, since
    #   after they are removed, they need to be rediscovered to get them
    #   back.
    # - Do not set this if the root file system is multipathed. In this
    #   case you want to set dev_loss_tmo to "infinity".  If you don't,
    #   then if all the path devices for your root file system disappear,
    #   udev will not be able to run to send the event to multipath to
    #   bring the device back.

    dev_loss_tmo                30

    # Supports large number of paths.
    #
    # The maximum number of file descriptors that can be opened by
    # multipath and multipathd. Should be set to the maximum number of
    # paths plus 32.

    max_fds                     4096
}

# Remove devices entries when overrides section is available.
devices {
    device {
        # These settings overrides built-in devices settings. It does
        # not apply to devices without built-in settings (these use the
        # settings in the "defaults" section), or to devices defined in
        # the "devices" section.
        all_devs                yes
        no_path_retry           %(no_path_retry)d
    }
}

# Enable when this section is available on all supported platforms.
# Options defined here override device specific options embedded into
# multipathd.
#
# overrides {
#      no_path_retry            %(no_path_retry)d
# }

""" % {"current_tag": _CURRENT_TAG,
       "no_path_retry": _NO_PATH_RETRY}

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
