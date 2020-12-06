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
This module provides multipath configuration functionality over the host.
Currently it only provides blacklist configuration for setting WWIDs of
disk devices which should be excluded from multipath management.
"""
import io
import logging
import os
import re

from . import fileUtils

_VDSM_MULTIPATH_BLACKLIST = '/etc/multipath/conf.d/vdsm_blacklist.conf'

_HEADER = """\
# This file is managed by vdsm, do not edit!
# Any changes made to this file will be overwritten when running:
# vdsm-tool config-lvm-filter

"""


log = logging.getLogger("storage.mpathconf")


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
