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
This module implements the replacement for libvirt.virConnection objects.
"""

from __future__ import absolute_import

import logging
import uuid

import libvirt

from . import domain
from . import doms
from . import errors
from . import events


class Connection(object):

    _log = logging.getLogger('virt.containers.Connection')

    def __init__(self):
        self.events = events.Handler(
            name='Connection(%s)' % id(self),
            parent=events.root
        )

    def close(self):
        """
        Does nothing succesfully
        """

    def domainEventRegisterAny(self, dom, eventID, cb, opaque):
        handler = events.root if dom is None else dom.events
        self._log.debug(
            '[%s] using handler %r for %i',
            self._name, handler, eventID)
        handler.register(eventID, self, dom, cb, opaque)

    def listAllDomains(self, flags=0):
        # flags are unused
        return doms.get_all()

    def listDomainsID(self):
        return [dom.ID() for dom in doms.get_all()]

    def lookupByUUIDString(self, guid):
        self._log.debug('looking for container %r', guid)
        try:
            dom = doms.get_by_uuid(guid)
        except KeyError:
            errors.throw(code=libvirt.VIR_ERR_NO_DOMAIN)
        else:
            self._log.debug('found container %r', guid)
            return dom

    def lookupByID(self, intid):
        return self.lookupByUUIDString(str(uuid.UUID(int=intid)))

    def getAllDomainStats(self, flags):
        return self.domainListGetStats(doms.get_all(), flags)

    def domainListGetStats(self, doms, flags):
        raise NotImplementedError  # not yet!

    def createXML(self, domxml, flags):
        return domain.Domain.create(domxml)

    def getLibVersion(self):
        return 0x001002018  # TODO

    def __getattr__(self, name):
        # virConnect does not expose non-callable attributes.
        return self._fake_method

    def _fake_method(self, *args):
        errors.throw()
