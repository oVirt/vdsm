#
# Copyright 2020 Red Hat, Inc.
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
#

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import libvirt

from testlib import maybefail

import vmfakelib as fake


class FakeCheckpoint(object):

    def __init__(self, checkpoint_xml):
        self.xml = checkpoint_xml

    def getXMLDesc(self):
        return self.xml


class FakeDomainAdapter(object):
    """
    FakeDomainAdapter mock a code that is depending on libvirt backup
    calls, using it will allow test the code without running a
    libvirt daemon.

    You can also simulate libvirtError by adding a key with the name of the
    method that should raise libvirt error to self.errors, the value
    will be the error code to raise, before calling a method.

    dom.errors['backupBegin'] = vmfakelib.libvirt_error(
        [libvirt.VIR_ERR_NO_DOMAIN_BACKUP], "Some libvirt error")

    Another option is to set custom backup and/or checkpoint XML
    as a response to backupGetXMLDesc() and/or checkpointLookupByName() by
    providing output_backup_xml/checkpoint_xml when creating the
    FakeDomainAdapter instance.

    dom = FakeDomainAdapter(
        output_backup_xml=output_backup_xml,
        output_checkpoint_xml=output_checkpoint_xml)

    To test a code using DomainAdapter:

        from virt.fakedomainadapter import FakeDomainAdapter

        def test_backup_XXX():
            ...

            dom = FakeDomainAdapter()
            dom.backupBegin(BACKUP_UNIX_XML, None)
            ...
    """

    def __init__(self, output_backup_xml=None, output_checkpoint_xml=None):
        self.backing_up = False
        self.input_backup_xml = None
        self.input_checkpoint_xml = None
        self.output_backup_xml = output_backup_xml
        if output_checkpoint_xml:
            self.output_checkpoint_xml = FakeCheckpoint(output_checkpoint_xml)
        else:
            self.output_checkpoint_xml = None
        self.errors = {}

    @maybefail
    def backupBegin(self, backup_xml, checkpoint_xml, flags=None):
        if self.backing_up:
            raise libvirt.libvirtError("backup already running for that VM")

        self.input_backup_xml = backup_xml
        self.input_checkpoint_xml = checkpoint_xml

        self.backing_up = True
        return 0

    @maybefail
    def abortJob(self, flags=None):
        if not self.backing_up:
            raise libvirt.libvirtError("no domain backup job found")

        self.backing_up = False
        return

    @maybefail
    def backupGetXMLDesc(self, flags=None):
        if not self.backing_up:
            raise libvirt.libvirtError("no domain backup job found")

        return self.output_backup_xml

    @maybefail
    def blockInfo(self, drive_name, flags=None):
        return (1024, 0, 0)

    @maybefail
    def checkpointLookupByName(self, checkpoint_id):
        if self.input_checkpoint_xml is None:
            raise fake.libvirt_error(
                [libvirt.VIR_ERR_NO_DOMAIN_CHECKPOINT], "Checkpoint not found")
        return FakeCheckpoint(self.input_checkpoint_xml)

    @maybefail
    def listAllCheckpoints(self, flags=None):
        # TODO: will be implemented when adding tests
        # for list and redefine checkpoints
        return list()
