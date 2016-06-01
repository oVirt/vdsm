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

from vdsm.network import netswitch

from testlib import VdsmTestCase as TestCaseBase
from nose.plugins.attrib import attr


@attr(type='unit')
class SplitSetupActionsTests(TestCaseBase):

    def test_split_nets(self):
        net_query = {'net2add': {'nic': 'eth0'},
                     'net2edit': {'nic': 'eth1'},
                     'net2remove': {'remove': True}}
        running_nets = {'net2edit': {'foo': 'bar'}}

        nets2add, nets2edit, nets2remove = netswitch._split_setup_actions(
            net_query, running_nets)

        self.assertEqual(nets2add, {'net2add': {'nic': 'eth0'}})
        self.assertEqual(nets2edit, {'net2edit': {'nic': 'eth1'}})
        self.assertEqual(nets2remove, {'net2remove': {'remove': True}})

    def test_split_bonds(self):
        bond_query = {'bond2add': {'nics': ['eth0', 'eth1']},
                      'bond2edit': {'nics': ['eth2', 'eth3']},
                      'bond2remove': {'remove': True}}
        running_bonds = {'bond2edit': {'foo': 'bar'}}

        bonds2add, bonds2edit, bonds2remove = netswitch._split_setup_actions(
            bond_query, running_bonds)

        self.assertEqual(bonds2add, {'bond2add': {'nics': ['eth0', 'eth1']}})
        self.assertEqual(bonds2edit, {'bond2edit': {'nics': ['eth2', 'eth3']}})
        self.assertEqual(bonds2remove, {'bond2remove': {'remove': True}})
