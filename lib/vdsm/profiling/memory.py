#
# Copyright 2014-2017 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division
"""
This module provides memory profiling.
"""

import logging
import threading

from vdsm.common import concurrent
from vdsm.common.logutils import traceback
from vdsm.config import config

from .errors import UsageError

# Import modules lazily when profile is started
dowser = None
cherrypy = None

_lock = threading.Lock()
_thread = None


def start():
    """ Starts application memory profiling """
    if is_enabled():
        _start_profiling()


def stop():
    """ Stops application memory profiling """
    if is_enabled():
        _stop_profiling()


def is_enabled():
    return config.getboolean('devel', 'memory_profile_enable')


def is_running():
    return _thread is not None


@traceback()
def _memory_viewer():
    cherrypy.tree.mount(dowser.Root())

    cherrypy.config.update({
        'server.socket_host': '0.0.0.0',
        'server.socket_port': config.getint('devel', 'memory_profile_port')})

    cherrypy.engine.start()


def _start_profiling():
    global cherrypy
    global dowser
    global _thread

    logging.debug("Starting memory profiling")

    import cherrypy
    import dowser
    # this nonsense makes pyflakes happy
    cherrypy
    dowser

    with _lock:
        if is_running():
            raise UsageError('Memory profiler is already running')
        _thread = concurrent.thread(_memory_viewer, name='memprofile')
        _thread.start()


def _stop_profiling():
    global _thread
    logging.debug("Stopping memory profiling")
    with _lock:
        if is_running():
            cherrypy.engine.exit()
            cherrypy.engine.block()
            _thread.join()
            _thread = None
