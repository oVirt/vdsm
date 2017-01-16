# Copyright 2017 Red Hat, Inc.
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

from __future__ import absolute_import
import collections
import io
import re

# A comment with key: value pair
META_COMMENT = re.compile(br"^#(\w+):\s*(.*?)\s*$")

# Describe a configurion file managed by vdsm.
#
# Arguments:
#   revision (str): the configuration file version
#   private (bool): this file is owned by the system administrator, and vdsm
#       must not touch it.
ConfigMetadata = collections.namedtuple("ConfigMetadata", "revision,private")


def read_metadata(path):
    values = parse_meta_comments(path)
    try:
        revision = int(values[b"REVISION"])
    except KeyError:
        revision = None
    try:
        private = boolify(values[b"PRIVATE"])
    except KeyError:
        private = False
    return ConfigMetadata(revision, private)


def parse_meta_comments(path):
    values = {}
    with io.open(path, "rb") as f:
        for line in f:
            if not line.startswith(b"#"):
                break
            match = META_COMMENT.search(line)
            if match:
                key, value = match.groups()
                values[key] = value
    return values


def boolify(value):
    if value == b"YES":
        return True
    elif value == b"NO":
        return False
    else:
        raise ValueError("Invalid boolean value: %s" % value)
