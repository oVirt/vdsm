# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later


# Before 4.20.34-1 (ovirt-4.2.5), metadata was cleared by writing invalid
# metadta.
CLEARED_VOLUME_METADATA = b"NONE=" + (b"#" * 502) + b"\nEOF\n"
