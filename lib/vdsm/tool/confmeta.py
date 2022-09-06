# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
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
