# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
This module is left behind as a proxy to the relocated logutils module.
The two proxy functions have been referenced in logger.conf, therefore,
any user that may have customized the logger.conf, would not get the updated
version with the new references.

TODO: The configuration in these specific entries in the logger.config.in
should be drop, letting the application define them (without exposing this
to users).
"""
from __future__ import absolute_import

from vdsm.common.logutils import (  # NOQA: F401 (unused import)
    TimezoneFormatter,
    UserGroupEnforcingHandler,
)
