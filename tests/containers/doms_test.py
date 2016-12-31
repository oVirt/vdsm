from __future__ import absolute_import
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


import uuid

from six.moves import range

from vdsm.virt.containers import doms

from . import conttestlib


NUM = 5  # random low value


class DomsTests(conttestlib.TestCase):

    def tearDown(self):
        conttestlib.clear_doms()

    def test_empty(self):
        self.assertEqual(doms.get_all(), [])

    def test_add(self):
        for _ in range(NUM):
            dom = FakeDomain(str(uuid.uuid4()))
            self.assertNotRaises(doms.add,
                                 dom)

    def test_remove(self):
        dom_list = _fill_doms(NUM)
        # random pick
        doms.remove(dom_list[2].uuid)
        for dom in doms.get_all():  # FIXME: better avoid this.
            self.assertNotEquals(dom.UUIDString(),
                                 dom_list[2].uuid)

    def test_get_all(self):
        dom_list = _fill_doms(NUM)
        all_doms = doms.get_all()
        self.assertEqual(len(all_doms), len(dom_list))
        dom_uuids = set(d.uuid for d in dom_list)
        for dom in all_doms:
            self.assertIn(dom.UUIDString(), dom_uuids)

    def test_get_by_uuid(self):
        dom_list = _fill_doms(NUM)
        # random pick
        self.assertEqual(dom_list[1],
                         doms.get_by_uuid(dom_list[1].uuid))


def _fill_doms(num):
    dom_list = []
    for _ in range(NUM):
        dom = FakeDomain(str(uuid.uuid4()))
        dom_list.append(dom)
        doms.add(dom)
    return dom_list


class FakeDomain(object):
    def __init__(self, uuid_str):
        self._uuid_str = uuid_str

    @property
    def uuid(self):
        # shortcut
        return self._uuid_str

    def UUIDString(self):
        return self._uuid_str

    def __eq__(self, other):
        return self._uuid_str == other._uuid_str

    def __hash__(self):
        return hash(self._uuid_str)
