#
# Copyright 2016-2020 Red Hat, Inc.
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

import libvirt

from vdsm.common import libvirtconnection
from vdsm.common import response
from vdsm.virt import recovery


from monkeypatch import MonkeyPatchScope
from monkeypatch import Patch
from testlib import VdsmTestCase as TestCaseBase
from testlib import permutations, expandPermutations

from . import vmfakelib as fake


_MINIMAL_EXTERNAL_DOMAIN_TEMPLATE = u'''<?xml version="1.0" encoding="UTF-8"?>
    <domain type="kvm"
        xmlns:ovirt-tune="http://ovirt.org/vm/tune/1.0"
        xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
    <name>{vm_name}</name>
    <uuid>{vm_uuid}</uuid>
</domain>'''


_MINIMAL_DOMAIN_TEMPLATE = u'''<?xml version="1.0" encoding="UTF-8"?>
    <domain type="kvm"
        xmlns:ovirt-tune="http://ovirt.org/vm/tune/1.0"
        xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
    <name>{vm_name}</name>
    <uuid>{vm_uuid}</uuid>
    <metadata>
        <ovirt-tune:qos></ovirt-tune:qos>
        <ovirt-vm:vm>
            <clusterVersion>4.2</clusterVersion>
        </ovirt-vm:vm>
    </metadata>
</domain>'''


def _raise(*args, **kwargs):
    raise err_no_domain()


def _error(*args, **kwargs):
    return response.error('MissParam')


@expandPermutations
class TestAllDomains(TestCaseBase):

    def setUp(self):
        self.vm_uuids = ('a', 'b',)
        self.vm_is_ext = [False] * len(self.vm_uuids)
        self.cif = fake.ClientIF()
        self.conn = FakeConnection()

        self.patch = Patch([
            (libvirtconnection, 'get', lambda *args, **kwargs: self.conn),
        ])
        self.patch.apply()

        # must be after patch.apply()
        self.conn.domains = _make_domains_collection(
            list(zip(self.vm_uuids, self.vm_is_ext))
        )

    def tearDown(self):
        self.patch.revert()

    def test_recover_no_domains(self):
        self.conn.domains = {}
        recovery.all_domains(self.cif)
        assert self.cif.vmRequests == {}

    def test_recover_few_domains(self):
        recovery.all_domains(self.cif)
        assert set(self.cif.vmRequests.keys()) == \
            set(self.vm_uuids)
        assert self.vm_is_ext == \
            [conf['external'] for conf, _ in self.cif.vmRequests.values()]

    @permutations([
        # create_fn
        (_raise,),
        (_error,),
    ])
    def test_recover_failures(self, create_fn):
        """
        We find VMs to recover through libvirt, but Vdsm fail to create
        its Vm objects. We should then destroy those VMs.
        """
        with MonkeyPatchScope([
            (self.cif, 'createVm', create_fn)
        ]):
            recovery.all_domains(self.cif)
        assert self.cif.vmRequests == \
            {}
        assert all(
            vm.destroyed for vm in self.conn.domains.values()
        )

    def test_domain_error(self):
        """
        We find VMs to recover through libvirt, but we get a failure trying
        to identify (UUIDString, XMLDesc) a domain being recovered
        """
        self.conn.domains['a'].XMLDesc = _raise
        recovery.all_domains(self.cif)
        assert set(self.cif.vmRequests.keys()) == \
            set(('b',))

    def test_recover_and_destroy_failure(self):
        """
        We find VMs to recover through libvirt, but Vdsm fail to create
        its Vm objects. We should then destroy those VMs, but one of
        the domains can't complete that. We should handle this case
        gracefully
        """
        self.conn.domains['b'].destroy = _raise
        with MonkeyPatchScope([
            (self.cif, 'createVm', _error)
        ]):
            recovery.all_domains(self.cif)
        assert self.cif.vmRequests == \
            {}
        assert self.conn.domains['a'].destroyed
        assert not self.conn.domains['b'].destroyed

    def test_external_vm(self):
        vm_infos = (('a', True), ('b', False),)
        self.conn.domains = _make_domains_collection(vm_infos)
        recovery.all_domains(self.cif)
        assert set(self.cif.vmRequests.keys()) == \
            set(vm_id for vm_id, _ in vm_infos)

        for vm_id, vm_is_ext in vm_infos:
            conf, _ = self.cif.vmRequests[vm_id]
            assert vm_is_ext == conf['external']

    def test_recover_external_vm_down(self):
        vm_is_ext = [True] * len(self.vm_uuids)
        self.conn.domains = _make_domains_collection(
            list(zip(self.vm_uuids, vm_is_ext))
        )
        for dom in self.conn.domains.values():
            dom.domState = libvirt.VIR_DOMAIN_SHUTOFF

        recovery.all_domains(self.cif)
        assert self.cif.vmRequests == {}

    def test_recover_external_vm_error(self):
        """
        handle gracefully error while getting the state of external VM
        """
        vm_is_ext = [True] * len(self.vm_uuids)
        self.conn.domains = _make_domains_collection(
            list(zip(self.vm_uuids, vm_is_ext))
        )
        for dom in self.conn.domains.values():
            dom.state = _raise

        recovery.all_domains(self.cif)
        assert self.cif.vmRequests == {}

    def test_external_vm_failure(self):
        """
        We find *external* VMs to recover through libvirt,
        but Vdsm fail to create its Vm objects.
        We should then destroy the non-external VMs.
        """
        vm_infos = (('a', True), ('b', False),)
        self.conn.domains = _make_domains_collection(vm_infos)
        with MonkeyPatchScope([
            (self.cif, 'createVm', _error)
        ]):
            recovery.all_domains(self.cif)
        assert self.cif.vmRequests == \
            {}
        for vm_id, vm_is_ext in vm_infos:
            vm_obj = self.conn.domains[vm_id]
            expect_destroy = not vm_is_ext
            assert vm_obj.destroyed == expect_destroy

    def test_lookup_external_vms(self):
        vm_ext = [True] * len(self.vm_uuids)
        self.conn.domains = _make_domains_collection(
            list(zip(self.vm_uuids, vm_ext)))
        self.cif.unknown_vm_ids = list(self.vm_uuids)
        recovery.lookup_external_vms(self.cif)
        assert set(self.cif.vmRequests.keys()) == \
            set(self.vm_uuids)
        assert vm_ext == \
            [conf['external'] for conf, _ in self.cif.vmRequests.values()]

    def test_lookup_external_vms_fails(self):
        """
        Failure to get the XML of an external VM while trying lookup
        """
        vm_ext = [True] * len(self.vm_uuids)
        self.conn.domains = _make_domains_collection(
            list(zip(self.vm_uuids, vm_ext)))
        self.conn.domains['a'].XMLDesc = _raise
        self.cif.unknown_vm_ids = list(self.vm_uuids)
        recovery.lookup_external_vms(self.cif)
        assert set(self.cif.vmRequests.keys()) == \
            set(('b',))


class FakeConnection(object):

    def __init__(self):
        self.domains = {}

    def lookupByUUIDString(self, vmid):
        try:
            return self.domains[vmid]
        except KeyError:
            raise err_no_domain()

    def listAllDomains(self):
        return list(self.domains.values())


def _make_domain_xml(vm_uuid, vm_name=None, external=False):
    if vm_name is None:
        vm_name = 'vm-%s' % vm_uuid
    if external:
        return _MINIMAL_EXTERNAL_DOMAIN_TEMPLATE.format(
            vm_uuid=vm_uuid, vm_name=vm_name
        )
    return _MINIMAL_DOMAIN_TEMPLATE.format(
        vm_uuid=vm_uuid, vm_name=vm_name
    )


def _make_domains_collection(vm_uuids):
    return {
        vm_uuid: fake.Domain(
            vmId=vm_uuid,
            xml=_make_domain_xml(
                vm_uuid,
                external=external
            ),
        )
        for vm_uuid, external in vm_uuids
    }


def err_no_domain():
    error = libvirt.libvirtError("No such domain")
    error.err = [libvirt.VIR_ERR_NO_DOMAIN]
    return error
