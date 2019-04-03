#
# Copyright 2019 Red Hat, Inc.
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

import functools
import libvirt

from vdsm.common import exception

from vdsm.virt import virdomain


# DomainAdapter should be defined only if libvirt supports
# incremental backup API
backup_enabled = hasattr(libvirt.virDomain, "backupBegin")


def requires_libvirt_support():
    """
    Decorator for prevent using backup methods to be
    called if libvirt doesn't supports incremental backup.
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*a, **kw):
            if not backup_enabled:
                raise exception.UnsupportedOperation(
                    "Libvirt version doesn't support "
                    "incremental backup operations"
                )
            return f(*a, **kw)
        return wrapper
    return decorator


if backup_enabled:
    @virdomain.expose(
        "backupBegin",
        "backupEnd",
        "backupGetXMLDesc",
        "checkpointCreateXML"
    )
    class DomainAdapter(object):
        """
        VM wrapper class that expose only
        libvirt backup related operations
        """
        def __init__(self, vm):
            self._vm = vm


def start_backup(
        vm, dom, backup_id, disks,
        from_checkpoint_id=None, to_checkpoint_id=None):
    raise exception.MethodNotImplemented()


def stop_backup(vm, dom, backup_id):
    raise exception.MethodNotImplemented()


def backup_info(vm, dom, backup_id):
    raise exception.MethodNotImplemented()


def delete_checkpoints(vm, dom, checkpoint_ids):
    raise exception.MethodNotImplemented()


def redefine_checkpoints(vm, dom, checkpoints):
    raise exception.MethodNotImplemented()
