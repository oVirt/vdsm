#
# Copyright 2013 Red Hat, Inc.
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
"""
This module is meant to be imported by vdsm in order to enable code coverage
"""
import os
import logging


log = logging.getLogger("code_coverage")
OPTION_NAME = 'VDSM_CODE_COVERAGE'
POSITIVE_ANSWERS = ('yes', 'true', 'on', '1')


def instrument():
    """
    enables code coverage, you can specify config file for coverage module
    """
    if OPTION_NAME not in os.environ:
        log.warn("Code coverage is dissabled. You need to export "
                 "%s variable to enable it", OPTION_NAME)
        return

    config = None
    enabled = os.environ[OPTION_NAME].strip()

    if enabled.lower() not in POSITIVE_ANSWERS:
        if not os.path.exists(enabled):
            log.warn("Found %s=%s; but expected is one from %s options "
                     "or path to existing config file.", OPTION_NAME, enabled,
                     POSITIVE_ANSWERS)
            log.warn("Code coverage is going to be skipped")
            return
        config = enabled

    start_msg = "Starting vdsm code coverage"
    if config:
        log.warn("%s with %s", start_msg, config)
        os.environ['COVERAGE_PROCESS_START'] = config
    else:
        log.warn(start_msg)

    from coverage.control import process_startup
    process_startup()


instrument()
