# Copyright 2014-2019 Red Hat, Inc.
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

from vdsm.network.netinfo import qos
from vdsm.network.tc import cls


class TestConversions(object):
    def test_qos_to_str(self):
        data = (({'ls': {'m1': 100, 'd': 10, 'm2': 300},
                  'ul': {'m1': 100, 'd': 10, 'm2': 300},
                  'rt': {'m1': 100, 'd': 10, 'm2': 300}},
                 {'ls': ['m1', '100bit', 'd', '10us', 'm2', '300bit'],
                  'ul': ['m1', '100bit', 'd', '10us', 'm2', '300bit'],
                  'rt': ['m1', '100bit', 'd', '10us', 'm2', '300bit']}),
                ({'ls': {'m1': 100, 'd': 10, 'm2': 300},
                  'rt': {'m1': 100, 'd': 10, 'm2': 300}},
                 {'ls': ['m1', '100bit', 'd', '10us', 'm2', '300bit'],
                  'rt': ['m1', '100bit', 'd', '10us', 'm2', '300bit']}),
                ({'ls': {'m1': 100, 'd': 10, 'm2': 300}},
                 {'ls': ['m1', '100bit', 'd', '10us', 'm2', '300bit']}))
        for input_qos, expected_str in data:
            assert cls._qos_to_str_dict(input_qos) == expected_str

    def test_get_root_qdisc(self):
        root = {'kind': 'hfsc', 'root': True, 'handle': '1:', 'refcnt': 2,
                'hfsc': {'default': 0x5000}}
        qdiscs = (root,
                  {'kind': 'sfq', 'handle': '10:', 'parent': '1:10',
                   'sfq': {'limit': 127, 'quantum': 1514}},
                  {'kind': 'sfq', 'handle': '20:', 'parent': '1:20',
                   'sfq': {'limit': 127, 'quantum': 1514}})
        assert qos.get_root_qdisc(qdiscs) == root
