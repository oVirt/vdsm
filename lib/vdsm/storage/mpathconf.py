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
- Configuration metadata parser.
"""
import io
import logging
import os
import re

from collections import namedtuple

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

log = logging.getLogger("storage.mpathconf")

Metadata = namedtuple("Metadata", "revision,private")


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
    first = second = ''
    with open(CONF_FILE) as f:
        mpathconf = [x.strip("\n") for x in f.readlines()]
    try:
        first = mpathconf[0]
        second = mpathconf[1]
    except IndexError:
        pass
    private = second.startswith(_PRIVATE_TAGS)

    if first.startswith(CURRENT_TAG):
        return Metadata(REVISION_OK, private)

    if first.startswith(_OLD_TAGS):
        return Metadata(REVISION_OLD, private)

    return Metadata(REVISION_MISSING, private)
