#
# Copyright 2014 Red Hat, Inc.
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
This module provides cpu profiling.
"""

import os
import logging

from vdsm import constants
from vdsm.config import config

# Import yappi only if profile is enabled
yappi = None


def start():
    global yappi
    if is_enabled():
        logging.debug("Starting profiling")
        import yappi
        yappi.start()


def stop():
    if is_running():
        logging.debug("Stopping profiling")
        yappi.stop()
        stats = yappi.get_func_stats()
        path = os.path.join(constants.P_VDSM_RUN, 'vdsmd.prof')
        stats.save(path, config.get('vars', 'profile_format'))


def is_enabled():
    return config.getboolean('vars', 'profile_enable')


def is_running():
    return yappi and yappi.is_running()
