# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

# Before 4.20.34-1 (ovirt-4.2.5), metadata was cleared by writing invalid
# metadta.
CLEARED_VOLUME_METADATA = b"NONE=" + (b"#" * 502) + b"\nEOF\n"
