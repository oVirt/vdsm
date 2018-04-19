#
# Copyright 2015-2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2 of the License, or
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

import errno
import os
import logging

from .domain import Domain
from .xmlfile import XMLFile
from . import doms
from . import monitoring
from . import runner
from . import xmlfile


_log = logging.getLogger('virt.containers')


def prepare():
    try:
        os.makedirs(xmlfile.STATE_DIR)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


def monitorAllDomains():
    """
    Must not require root privileges.
    """
    monitoring.watchdog(runner.Runner.get_all)


def recoveryAllDomains():
    for rt_uuid in runner.Runner.get_all():
        _log.debug('trying to recover container %r', rt_uuid)
        xml_file = XMLFile(rt_uuid)
        try:
            Domain.recover(rt_uuid, xml_file.read())
        except Exception:  # TODO: narrow this down
            _log.exception('failed to recover container %r', rt_uuid)
            logging.exception('failed to recover container %r', rt_uuid)
        else:
            _log.debug('recovered container %r', rt_uuid)
    return [(d, d.XMLDesc(0), False) for d in doms.get_all()]
