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
from vdsm.storage import fileUtils
from vdsm.storage import mpathconf
from vdsm.tool import service

from . import YES, NO

_MULTIPATHD = cmdutils.CommandPath("multipathd", "/usr/sbin/multipathd")

# Once multipathd notices that the last path has failed, it will check
# all paths "no_path_retry" more times. If no paths are up, it will tell
# the kernel to stop queuing.  After that, all outstanding and future
# I/O will immediately be failed, until a path is restored. Once a path
# is restored the delay is reset for the next time all paths fail.
_NO_PATH_RETRY = 16

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

    # Ensure the minimal time I/O is queued when no path is available.
    #
    # The number of retries until disable queueing, or "fail" for
    # immediate failure (no queueing), or "queue" for unlimited timeout.
    #
    # Using "queue" will cause commands run by vdsm (e.g. lvm) to get
    # stuck for unlimited time, causing timeouts in various flows, and
    # may cause a host to become non-responsive.
    #
    # Using "fail" will cause VMs to pause shortly after all paths have
    # failed. When using a HA VM with a storage lease, the lease will be
    # released. This may delay the time to restart the VM on another
    # host.
    #
    # The recommended setting is number of retries, keeping the VM
    # running during storage outage, for example during storage server
    # upgrades or failover scenarios.
    #
    # This value should be synchronized with sanlock lease renewal
    # timeout (8 * sanlock:io_timeout). This ensures that HA VMs with a
    # storage lease will be terminated by sanlock if their storage lease
    # expire, and will be started quickly on another host. If you change
    # sanlock:io_timeout you need to update this value.
    #
    # This value also depends on polling_interval (5 seconds). If you
    # change polling interval you need to update this value.
    #
    # The recommended setting:
    #
    #   8 * sanlock:io_timeout / polling_interval

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

# Blacklist local devices, obsolete protocols, and device nodes which
# should not be used with multipath.
#
# Complete list of protocols recognized by multipath:
# scsi:fcp        Fibre Channel
# scsi:spi        Parallel SCSI
# scsi:ssa        Serial Storage Architecture
# scsi:sbp        Firewire
# scsi:srp        Infiniband RDMA
# scsi:iscsi      Internet SCSI
# scsi:sas        Serial Attached Storage
# scsi:adt        Automation Drive Interface
# scsi:ata        Advanced Technology Attachment
# scsi:unspec     multipath unable to determine scsi transport
# ccw             Channel Command Words
# cciss           Command and Control Interface Subsystem
# nvme            Non-Volatile Memory Express
# undef           multipath unable to determine protocol
#
# We blacklist following protocols:
# scsi:adt  local storage protocol
# scsi:sbp  local storage protocol
#
# Other protocols are not blacklisted for following reasons:
# scsi:fcp      officially supported
# scsi:iscsi    officially supported
# scsi:sas      not supported, but not blacklisting it to avoid breaking
#               existing user setup, known to be used by users
# cciss         not supported, but not blacklisting it to avoid breaking
#               existing user setup, known to be used by users
# scsi:srp      not supported, but not blacklisting it to avoid breaking
#               existing user setup
# ccw           not supported, but not blacklisting it to avoid breaking
#               existing user setup
# nvme          can be used with multipath setup, protocols like NVMe-oF will
#               come up as nvme
# scsi:spi      superseded by scsi:fcp, not a local device, no need to
#               blacklist it
# scsi:ssa      superseded by scsi:fcp, not a local device, no need to
#               blacklist it
#
# Devices running scsi:ata and scsi:unspec are quite common and almost always
# cannot create multipath, so it would be good to blacklist them. But in
# general multipath can be create on top either of these devices, so we don't
# black list them to avoid breaking anyone's setup.
#
# You can make blacklist more restrictive by creating drop-in config file,
# e.g. /etc/multipath/conf.d/local.conf, and extend blacklist
# according to your needs, e.g.
#
#   blacklist {
#       protocol "(scsi:ata|scsi:unspec)"
#   }
#
# You can also add exceptions to blacklist (see man(5) multipath.conf for more
# details) by adding blacklist_exceptions to your drop-in configuration file,
# e.g.
#
#   blacklist_exceptions {
#       protocol "(scsi:spi|scsi:ssa)"
#   }
#
# We blacklist all RADOS Block Device (RBD) devices.
# When using Ceph, multipath prevents the rbd devices from being unmapped
# and the devices remain busy.

blacklist {
    protocol "(scsi:adt|scsi:sbp)"
    devnode "^(rbd)[0-9]*"
}

# Options defined here override device specific options embedded into
# multipathd.

overrides {
    # NOTE: see comments in default section how to compute this value.
    no_path_retry   %(no_path_retry)d
}

""" % {"current_tag": mpathconf.CURRENT_TAG,
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
    backup = fileUtils.backup_file(mpathconf.CONF_FILE)
    if backup:
        sys.stdout.write("Previous multipath.conf copied to %s\n" % backup)

    data = _CONF_DATA.encode('utf-8')
    fileUtils.atomic_write(mpathconf.CONF_FILE, data, relabel=True)

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
