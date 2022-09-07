# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.config import config

if config.getboolean('devel', 'coverage_enable'):
    import coverage  # pylint: disable=import-error
    coverage.process_startup()
