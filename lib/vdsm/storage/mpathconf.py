#
# Copyright 2020 Red Hat, Inc.
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

"""
This module provides multipath configuration functionality over the host:
- Blacklist configuration for setting WWIDs of disk devices
  which should be excluded from multipath management.
- Vdsm-supported multipathd configuration.
- Configuration metadata parser.
"""
import io
import logging
import os
import re

from collections import namedtuple
from string import Template

from vdsm.common import cmdutils
from vdsm.storage import multipath
from . import fileUtils

CONF_FILE = '/etc/multipath.conf'
_VDSM_MULTIPATH_BLACKLIST = '/etc/multipath/conf.d/vdsm_blacklist.conf'

_HEADER = """\
# This file is managed by vdsm, do not edit!
# Any changes made to this file will be overwritten when running:
# vdsm-tool config-lvm-filter

"""

# The first line of multipath.conf configured by vdsm must contain a
# "VDSM REVISION X.Y" tag.  Note that older version used "RHEV REVISION X.Y"
# format.

CURRENT_TAG = "# VDSM REVISION 2.2"

_OLD_TAGS = (
    "# VDSM REVISION 2.1",
    "# VDSM REVISION 2.0",
    "# VDSM REVISION 1.9",
    "# VDSM REVISION 1.8",
    "# VDSM REVISION 1.7",
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
_PRIVATE_TAGS = (_PRIVATE_TAG, _OLD_PRIVATE_TAG)

REVISION_OK = "OK"
REVISION_OLD = "OLD"
REVISION_MISSING = "MISSING"

# Once multipathd notices that the last path has failed, it will check
# all paths "no_path_retry" more times. If no paths are up, it will tell
# the kernel to stop queuing.  After that, all outstanding and future
# I/O will immediately be failed, until a path is restored. Once a path
# is restored the delay is reset for the next time all paths fail.
_NO_PATH_RETRY = 16

_CONF_DATA = Template("""\
$current_tag

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

    no_path_retry               $no_path_retry

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
    no_path_retry   $no_path_retry
}

""").substitute({"current_tag": CURRENT_TAG,
                 "no_path_retry": _NO_PATH_RETRY})

_SKIPPED_SECTIONS = ("blacklist", "blacklist_exceptions")

log = logging.getLogger("storage.mpathconf")

Metadata = namedtuple("Metadata", "revision,private")
Section = namedtuple("Section", "name,children")
Pair = namedtuple("Pair", "key,value")
Result = namedtuple("Result", "error,issues")


def configure_blacklist(wwids):
    """
    Configures a blacklist file /etc/multipath/conf.d/vdsm_blacklist.conf,
    the configuration file is private for VDSM, any other user customized
    blacklist conf file within the same path will be taken along with the
    blacklist specified in the vdsm_blacklist.conf file.

    Arguments:
        wwids (iterable): WWIDs to be blacklisted.
    """
    log.debug("Configure %s with blacklist WWIDs %s",
              _VDSM_MULTIPATH_BLACKLIST, wwids)

    try:
        os.makedirs(os.path.dirname(_VDSM_MULTIPATH_BLACKLIST))
    except FileExistsError:
        # directory already exists
        pass

    buf = io.StringIO()
    buf.write(_HEADER)
    buf.write(format_blacklist(wwids))
    data = buf.getvalue().encode("utf-8")

    fileUtils.atomic_write(_VDSM_MULTIPATH_BLACKLIST, data, relabel=True)


def format_blacklist(wwids):
    """
    Format the blacklist section string.

    blacklist {
        wwid "wwid1"
        wwid "wwid2"
        wwid "wwid3"
    }

    Arguments:
        wwids (iterable): WWIDs to include in the blacklist section.

    Returns:
        A formatted blacklist section string.
    """
    lines = "\n".join('    wwid "{}"'.format(w) for w in sorted(wwids))
    return """blacklist {{
{}
}}
""".format(lines)


def read_blacklist():
    """
    Read WWIDs from /etc/multipath/conf.d/vdsm_blacklist.conf

    Returns:
        A set of read WWIDs. Would be empty if file does not exist or no WWIDs
        are currently blacklisted.
    """
    wwids = set()
    if not os.path.exists(_VDSM_MULTIPATH_BLACKLIST):
        log.debug("Configuration file %s does not exist",
                  _VDSM_MULTIPATH_BLACKLIST)
        return wwids

    with open(_VDSM_MULTIPATH_BLACKLIST, "r") as f:
        blacklist = f.read()

    m = re.search(r"blacklist\s*\{(.*)\}", blacklist, re.DOTALL)
    if not m:
        log.warning("No blacklist section found in %s",
                    _VDSM_MULTIPATH_BLACKLIST)
        return wwids

    for line in m.group(1).splitlines():
        line = line.strip()
        fields = line.split()
        # multipathd only treats the first two fields per line: wwid "xxx"
        if len(fields) < 2 or fields[0] != "wwid":
            log.warning("Skipping invalid line: %r", line)
            continue
        if len(fields) > 2:
            log.warning("Ignoring extra data starting with %r", fields[2])

        wwid = fields[1].strip('"')
        wwids.add(wwid)

    return wwids


def configure_multipathd():
    """
    Set up multipath daemon configuration to the known and supported state.
    The original configuration, if any, is saved.

    Returns:
        str: The path to the copy of the old multipath.conf. Can be empty.
    """
    backup = fileUtils.backup_file(CONF_FILE)

    data = _CONF_DATA.encode('utf-8')
    fileUtils.atomic_write(CONF_FILE, data, relabel=True)
    return backup


def read_metadata():
    """
    The multipath configuration file at /etc/multipath.conf should contain
    a tag in form "RHEV REVISION X.Y" for this check to succeed.
    If the tag above is followed by tag "RHEV PRIVATE" (old format) or
    "VDSM PRIVATE" (new format), the configuration should be preserved
    at all cost.

    Returns:
        vdsm.storage.mpathconf.Metadata
    """
    with open(CONF_FILE) as f:
        # Read only the lines that are going to be processed
        first = f.readline()
        second = f.readline()

    private = second.startswith(_PRIVATE_TAGS)

    if first.startswith(CURRENT_TAG):
        return Metadata(REVISION_OK, private)

    if first.startswith(_OLD_TAGS):
        return Metadata(REVISION_OLD, private)

    return Metadata(REVISION_MISSING, private)


def check_local_config():
    """
    Check if 'user_friendly_names' is enabled in the multipathd configuration.
    The configuration is obtained by running the multipathd command to show
    the local configuration and capturing the output.
    Parse the output and check that the contents are valid.

    Returns:
        vdsm.storage.mpathconf.Result
    """
    error = None
    try:
        mpath_conf = _parse_conf(io.StringIO(multipath.show_config_local()))
        issues = _check_conf(mpath_conf)
    except cmdutils.Error as e:
        error = f"Cannot check multipath configuration: {e}"
        issues = []
    else:
        if issues:
            error = ("Invalid configuration: 'user_friendly_names' is "
                     "enabled in multipath configuration")
    return Result(error, issues)


def _check_conf(mpath_conf):
    """
    Check that 'user_friendly_names' is disabled in all the children
    of the parsed multipathd configuration.
    Search is recursive.

    Args:
        mpath_conf: Parsed structure of the multipathd configuration output.

    Returns:
        List[Section]: Sections that have invalid configuration.
    """
    issues = []
    for item in mpath_conf:
        if not item.children or item.name in _SKIPPED_SECTIONS:
            continue

        if all(isinstance(child, Section) for child in item.children):
            issues.extend(_check_conf(item.children))
        elif all(isinstance(child, Pair) for child in item.children):
            if Pair("user_friendly_names", "yes") in item.children:
                issues.append(item)
        else:
            raise ValueError(f"Invalid item: {item}")

    return issues


def _parse_conf(mpath_out_it):
    """
    Recursively parse multipath daemon configuration output.

    Args:
        mpath_out_it (iterator): String iterator containing the output of the
            multipath configuration.

    Returns:
        Parsed configuration.
    """
    res = []
    for line in mpath_out_it:
        line = line.strip()
        if line.endswith("{"):
            name, _ = line.rsplit(" ", 1)
            res.append(Section(name, _parse_conf(mpath_out_it)))
        elif line == "}":
            break
        else:
            if not line:
                # Discard empty lines
                continue
            key, value = line.split(" ", 1)
            if value[0] == value[-1] == '"':
                value = value[1:-1]
            res.append(Pair(key, value))
    return res
