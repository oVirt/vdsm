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
"""
This module implements the replacement for libvirt.virDomain objects.
"""

from __future__ import absolute_import

import logging
import uuid
import xml.etree.ElementTree as ET

import libvirt

from . import errors
from . import events
from . import docker
from . import doms
from . import runner
from . import xmlfile


class Domain(object):

    _log = logging.getLogger('virt.containers.Domain')

    @classmethod
    def create(cls, xmldesc):
        inst = cls(xmldesc)
        inst._startup()
        doms.add(inst)
        return inst

    @classmethod
    def recover(cls, rt_uuid, xmldesc):
        inst = cls(xmldesc, rt_uuid)
        inst._recover()
        doms.add(inst)
        return inst

    def __init__(self, xmldesc, rt_uuid=None):
        self._xmldesc = xmldesc
        self._root = ET.fromstring(xmldesc)
        self._vm_uuid = uuid.UUID(self._root.find('./uuid').text)
        self._log.debug('initializing docker container %r', self.UUIDString())
        self._rt = docker.Runtime(rt_uuid)
        self._xml_file = xmlfile.XMLFile(self._rt.uuid)
        self._log.debug('initializing container %r runtime %r',
                        self.UUIDString(), self._rt.uuid)
        self.events = events.Handler(
            name='Domain(%s)' % self._vm_uuid,
            parent=events.root)

    def createWithFlags(self, flags):
        pass

    def undefine(self):
        # For libvirt API compatibility, container domains are always transient
        # so this is no-op here.
        pass

    def destroyFlags(self, flags):
        #  flags are unused
        vm_uuid = self.UUIDString()

        self._log.debug('shutting down container %r', vm_uuid)
        try:
            self._shutdown()
            doms.remove(vm_uuid)
        # TODO: throw more specific errors
        except runner.OperationFailed:
            errors.throw()
        except KeyError:
            errors.throw()

    def destroy(self):
        return self.destroyFlags(0)

    def reset(self, flags):
        self._log.debug('resetting container %r', self.UUIDString())
        self._rt.stop()
        self._log.debug('stopped container %r', self.UUIDString())
        self._rt.start()
        self._log.debug('restarted container %r', self.UUIDString())

    def runtimeUUIDString(self):
        return str(self._rt.uuid)

    def ID(self):
        # pylint: disable=no-member
        # due to https://github.com/PyCQA/pylint/issues/961
        return self._vm_uuid.int

    def UUIDString(self):
        return str(self._vm_uuid)

    def XMLDesc(self, flags):
        # TODO: raise warning to signal we ignore flags?
        return self._xmldesc

    def controlInfo(self):
        # TODO: do it better
        return (libvirt.VIR_DOMAIN_CONTROL_OK, 0, 0)

#    def blockInfo(self, path, flags):
#        pass
#
#    def setTime(self, time):
#        pass

    def info(self):
        # TODO: check all but the first value
        return [libvirt.VIR_DOMAIN_RUNNING, 0, 0, 1, 0]

    def vcpus(self):
        # TODO: does this count as hack?
        return [[], []]

    def metadata(self):
        errors.throw(
            code=libvirt.VIR_ERR_NO_DOMAIN_METADATA
        )

    def _startup(self):
        self._log.debug('clearing XML cache for %r', self.UUIDString())
        self._xml_file.clear()
        self._log.debug('configuring container %r', self.UUIDString())
        self._rt.configure(self._root)
        self._log.debug('saving domain XML for %r', self.UUIDString())
        self._xml_file.save(self._root)
        self._log.debug('starting container %r', self.UUIDString())
        self._rt.start()
        self._log.debug('started container %r', self.UUIDString())

    def _recover(self):
        self._log.debug('recovering container %r', self.UUIDString())
        self._rt.recover()
        self._log.debug('recovered container %r', self.UUIDString())

    def _shutdown(self):
        self._log.debug('shutting down container %r', self.UUIDString())
        self._rt.stop()
        self._log.debug('stopped container %r', self.UUIDString())
        self._xml_file.clear()
        self._log.debug('turn down container %r', self.UUIDString())

    def __getattr__(self, name):
        # virDomain does not expose non-callable attributes.
        return self._fake_method

    def _fake_method(self, *args):
        errors.throw()
