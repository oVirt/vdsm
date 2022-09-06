# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Internal errors.

For public error see lib.vdsm.common.exception.
"""

from vdsm.common.errors import Base  # noqa: F401 (unused import)


class StorageUnavailableError(Exception):
    pass
