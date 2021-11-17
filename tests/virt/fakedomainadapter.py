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

import libvirt

from testlib import maybefail
from testlib import normalized

from . import vmfakelib as fake


class FakeCheckpoint(object):

    def __init__(self, checkpoint_xml, name, dom=None):
        self.xml = checkpoint_xml
        self.name = name
        self.errors = {}
        self.dom = dom

    def __eq__(self, other):
        return self.name == other.name

    @maybefail
    def getXMLDesc(self):
        return self.xml

    def getName(self):
        return self.name

    @maybefail
    def delete(self):
        # Deleting a checkpoint will not update the next checkpoint
        # in the chain as libvirt does, this part is currently not tested.
        self.dom.output_checkpoints.remove(self)
        self.dom = None


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
    providing output_backup_xml/output_checkpoints when creating the
    FakeDomainAdapter instance.

    dom = FakeDomainAdapter(
        output_backup_xml=output_backup_xml,
        output_checkpoints=[fakeCheckpoint1, fakeCheckpoint2])

    To test a code using DomainAdapter:

        from virt.fakedomainadapter import FakeDomainAdapter

        def test_backup_XXX():
            ...

            dom = FakeDomainAdapter()
            dom.backupBegin(BACKUP_UNIX_XML, None)
            ...
    """

    def __init__(self, output_backup_xml=None, output_checkpoints=()):
        self.backing_up = False
        self.input_backup_xml = None
        self.input_checkpoint_xml = None
        self.output_backup_xml = output_backup_xml
        self.output_checkpoints = list(output_checkpoints)
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
    def blockInfo(self, drive_name, flags=0):
        return (1024, 0, 0)

    @maybefail
    def checkpointLookupByName(self, checkpoint_id):
        for checkpoint in self.output_checkpoints:
            if checkpoint.getName() == checkpoint_id:
                return checkpoint

        raise fake.libvirt_error(
            [libvirt.VIR_ERR_NO_DOMAIN_CHECKPOINT], "Checkpoint not found")

    @maybefail
    def listAllCheckpoints(self, flags=None):
        return list(self.output_checkpoints)

    @maybefail
    def checkpointCreateXML(self, checkpoint_xml, flags=None):
        expected_flags = (
            libvirt.VIR_DOMAIN_CHECKPOINT_CREATE_REDEFINE |
            libvirt.VIR_DOMAIN_CHECKPOINT_CREATE_REDEFINE_VALIDATE
        )
        assert flags == expected_flags

        # validate the given checkpoint XML according to the
        # initialized output_checkpoints, in case output_checkpoints
        # isn't initialized the validation will be skipped
        if self.output_checkpoints:
            normalized_checkpoint_xml = normalized(checkpoint_xml)
            for checkpoint in self.output_checkpoints:
                expected_checkpoint_xml = normalized(checkpoint.getXMLDesc())
                if normalized_checkpoint_xml == expected_checkpoint_xml:
                    return

            raise fake.libvirt_error(
                [libvirt.VIR_ERR_INVALID_DOMAIN_CHECKPOINT,
                 '', "Invalid checkpoint error"],
                "Fake checkpoint error")
