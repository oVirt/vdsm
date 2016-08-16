# Copyright 2016 Red Hat, Inc.
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

from contextlib import contextmanager

from nose.plugins.attrib import attr
from nose.plugins.skip import SkipTest

from testlib import VdsmTestCase as TestCaseBase

from .nettestlib import dummy_devices

from vdsm.network.link import iface
from vdsm.network.link.bond import Bond
from vdsm.network.link.bond import BondSysFS
from vdsm.utils import random_iface_name


def setup_module():
    if not _has_sysfs_bond_permission():
        raise SkipTest("This test requires sysfs bond write access")


@attr(type='integration')
class LinkBondTests(TestCaseBase):

    def test_bond_without_slaves(self):
        with bond_device() as bond:
            self.assertFalse(iface.is_up(bond.master))

    def test_bond_with_slaves(self):
        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as bond:
                bond.add_slaves((nic1, nic2))
                self.assertFalse(iface.is_up(bond.master))

    def test_bond_devices_are_up(self):
        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as bond:
                bond.add_slaves((nic1, nic2))
                bond.up()
                self.assertTrue(iface.is_up(nic1))
                self.assertTrue(iface.is_up(nic2))
                self.assertTrue(iface.is_up(bond.master))

    def test_bond_exists(self):
        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as _bond:
                _bond.add_slaves((nic1, nic2))
                _bond.up()

                bond = Bond(_bond.master)
                self.assertEqual(bond.slaves, set((nic1, nic2)))
                # TODO: Support options
                self.assertEqual(bond.options, None)


@contextmanager
def bond_device(prefix='bond_', max_length=11):
    bond_name = random_iface_name(prefix, max_length)
    bond = Bond(bond_name)
    bond.create()
    try:
        yield bond
    finally:
        bond.destroy()


def _has_sysfs_bond_permission():
    bond = BondSysFS(random_iface_name('check_', max_length=11))
    try:
        bond.create()
        bond.destroy()
    except IOError:
        return False
    return True
