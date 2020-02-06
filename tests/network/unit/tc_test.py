#
# Copyright 2012 Roman Fenkhuber.
# Copyright 2012-2019 Red Hat, Inc.
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
import os

from six.moves import zip_longest

from vdsm.network import tc


class TestFilters(object):
    def test_filter_objs(self):
        dirName = os.path.dirname(os.path.realpath(__file__))
        path = os.path.join(dirName, "tc_filter_show.out")
        with open(path) as f:
            out = f.read()
        PARSED_FILTERS = (
            tc.Filter(
                prio=49149,
                handle='803::800',
                actions=[tc.MirredAction(target='tap1')],
            ),
            tc.Filter(
                prio=49150,
                handle='802::800',
                actions=[tc.MirredAction(target='tap2')],
            ),
            tc.Filter(
                prio=49152,
                handle='800::800',
                actions=[
                    tc.MirredAction(target='target'),
                    tc.MirredAction(target='target2'),
                ],
            ),
        )
        assert tuple(tc.filters('bridge', 'parent', out=out)) == PARSED_FILTERS

    def test_filters(self):
        filters = (
            {
                'protocol': 'all',
                'pref': 168,
                'kind': 'basic',
                'parent': '1389:',
                'basic': {},
            },
            {
                'protocol': 'all',
                'pref': 168,
                'kind': 'basic',
                'parent': '1389:',
                'basic': {
                    'flowid': '1389:a8',
                    'handle': '0x1',
                    'mask': 0,
                    'module': 'meta',
                    'object': 'vlan',
                    'relation': 'eq',
                    'value': 168,
                },
            },
            {
                'protocol': 'all',
                'pref': 168,
                'kind': 'basic',
                'parent': '1389:',
                'basic': {
                    'flowid': '1389:a8',
                    'handle': '0x1',
                    'mask': 0,
                    'module': 'meta',
                    'object': 'vlan',
                },
            },
            {
                'protocol': 'all',
                'pref': 168,
                'kind': 'basic',
                'parent': '1389:',
                'basic': {
                    'module': 'meta',
                    'flowid': '1389:a8',
                    'handle': '0x1',
                },
            },
            {'protocol': 'all', 'pref': 49149, 'kind': 'u32', 'u32': {}},
            {
                'protocol': 'all',
                'pref': 49149,
                'kind': 'u32',
                'u32': {'fh': '803:', 'ht_divisor': 1},
            },
            {
                'protocol': 'all',
                'pref': 49149,
                'kind': 'u32',
                'u32': {
                    'fh': '803::800',
                    'order': 2048,
                    'key_ht': 0x803,
                    'key_bkt': 0x0,
                    'terminal': True,
                    'match': {'value': 0x0, 'mask': 0x0, 'offset': 0x0},
                    'actions': [
                        {
                            'order': 1,
                            'kind': 'mirred',
                            'action': 'egress_mirror',
                            'target': 'tap1',
                            'op': 'pipe',
                            'index': 18,
                            'ref': 1,
                            'bind': 1,
                        }
                    ],
                },
            },
            {'protocol': 'all', 'pref': 49150, 'kind': 'u32', 'u32': {}},
            {
                'protocol': 'all',
                'pref': 49150,
                'kind': 'u32',
                'u32': {'fh': '802:', 'ht_divisor': 1},
            },
            {
                'protocol': 'all',
                'pref': 49150,
                'kind': 'u32',
                'u32': {
                    'fh': '802::800',
                    'order': 2048,
                    'key_ht': 0x802,
                    'key_bkt': 0x0,
                    'terminal': True,
                    'match': {'value': 0x0, 'mask': 0x0, 'offset': 0x0},
                    'actions': [
                        {
                            'order': 33,
                            'kind': 'mirred',
                            'action': 'egress_mirror',
                            'target': 'tap2',
                            'op': 'pipe',
                            'index': 17,
                            'ref': 1,
                            'bind': 1,
                        }
                    ],
                },
            },
            {'protocol': 'all', 'pref': 49152, 'kind': 'u32', 'u32': {}},
            {
                'protocol': 'all',
                'pref': 49152,
                'kind': 'u32',
                'u32': {'fh': '800:', 'ht_divisor': 1},
            },
            {
                'protocol': 'all',
                'pref': 49152,
                'kind': 'u32',
                'u32': {
                    'fh': '800::800',
                    'order': 2048,
                    'key_ht': 0x800,
                    'key_bkt': 0x0,
                    'terminal': True,
                    'match': {'value': 0x0, 'mask': 0x0, 'offset': 0x0},
                    'actions': [
                        {
                            'order': 1,
                            'kind': 'mirred',
                            'action': 'egress_mirror',
                            'target': 'target',
                            'op': 'pipe',
                            'index': 60,
                            'ref': 1,
                            'bind': 1,
                        },
                        {
                            'order': 2,
                            'kind': 'mirred',
                            'action': 'egress_mirror',
                            'target': 'target2',
                            'op': 'pipe',
                            'index': 61,
                            'ref': 1,
                            'bind': 1,
                        },
                    ],
                },
            },
        )
        dirName = os.path.dirname(os.path.realpath(__file__))
        path = os.path.join(dirName, "tc_filter_show.out")
        with open(path) as tc_filter_show:
            data = tc_filter_show.read()

        for parsed, correct in zip_longest(
            tc._filters(None, out=data), filters
        ):
            assert parsed == correct

    def test_qdiscs(self):
        data_lines = (
            'qdisc hfsc 1: root refcnt 2 default 5000',
            'qdisc ingress ffff: parent ffff:fff1 ----------------',
            'qdisc mq 0: dev wlp3s0 root',
            'qdisc ingress ffff: dev vdsmtest-Z2TMO parent ffff:fff1 '
            '----------------',  # end of previous line
            'qdisc pfifo_fast 0: dev em1 root refcnt 2 bands 3 priomap  '
            '1 2 2 2 1 2 0 0 1 1 1 1 1 1 1 1',  # end of previous line
            'qdisc pfifo_fast 0: dev wlp3s0 parent :1 bands 3 priomap  '
            '1 2 2 2 1 2 0 0 1 1 1 1 1 1 1 1',  # end of previous line
            'qdisc fq_codel 801e: root refcnt 2 limit 132p flows 15 quantum '
            '400 target 5.0ms interval 150.0ms ecn',  # end of previous line
        )
        data = '\n'.join(data_lines)
        qdiscs = (
            {
                'kind': 'hfsc',
                'root': True,
                'handle': '1:',
                'refcnt': 2,
                'hfsc': {'default': 0x5000},
            },
            {'kind': 'ingress', 'handle': 'ffff:', 'parent': 'ffff:fff1'},
            {'kind': 'mq', 'handle': '0:', 'dev': 'wlp3s0', 'root': True},
            {
                'kind': 'ingress',
                'handle': 'ffff:',
                'dev': 'vdsmtest-Z2TMO',
                'parent': 'ffff:fff1',
            },
            {
                'kind': 'pfifo_fast',
                'handle': '0:',
                'dev': 'em1',
                'root': True,
                'refcnt': 2,
                'pfifo_fast': {
                    'bands': 3,
                    'priomap': [1, 2, 2, 2, 1, 2, 0, 0, 1, 1, 1, 1, 1, 1, 1],
                },
            },
            {
                'kind': 'pfifo_fast',
                'handle': '0:',
                'dev': 'wlp3s0',
                'parent': ':1',
                'pfifo_fast': {
                    'bands': 3,
                    'priomap': [1, 2, 2, 2, 1, 2, 0, 0, 1, 1, 1, 1, 1, 1, 1],
                },
            },
            {
                'kind': 'fq_codel',
                'handle': '801e:',
                'root': True,
                'refcnt': 2,
                'fq_codel': {
                    'limit': 132,
                    'flows': 15,
                    'quantum': 400,
                    'target': 5000.0,
                    'interval': 150000.0,
                    'ecn': True,
                },
            },
        )
        for parsed, correct in zip_longest(tc.qdiscs(None, out=data), qdiscs):
            assert parsed == correct

    def test_classes(self):
        cmd_line_ls_10 = 3200
        cmd_line_ls_m1_20 = 6400
        cmd_line_ls_d_20 = 152
        cmd_line_ls_m2_20 = 3200
        cmd_line_ls_30 = 3500
        cmd_line_ls_m2_5000 = 40000
        data = '\n'.join(
            (
                'class hfsc 1: root',
                'class hfsc 1:10 parent 1: leaf 10: sc m1 0bit d 0us '
                'm2 {0}Kbit'.format(cmd_line_ls_10),  # end of previous line
                'class hfsc 1:20 parent 1: leaf 20: ls m1 {0}Kibit d {1}us '
                'm2 {2}Kbit ul m1 0bit d 0us m2 30000Kbit'.format(
                    cmd_line_ls_m1_20, cmd_line_ls_d_20, cmd_line_ls_m2_20
                ),
                'class hfsc 1:30 parent 1: leaf 40: sc m1 0bit d 0us '
                'm2 {0}bit'.format(cmd_line_ls_30),  # end of previous line
                'class hfsc 1:5000 parent 1: leaf 5000: ls m1 0bit d 0us '
                'm2 {0}Kbit'.format(
                    cmd_line_ls_m2_5000
                ),  # end of previous line
            )
        )
        reported_ls_10 = cmd_line_ls_10 * 1000 // 8
        reported_ls_m1_20 = cmd_line_ls_m1_20 * 1024 // 8
        reported_ls_d_20 = cmd_line_ls_d_20 // 8
        reported_ls_m2_20 = cmd_line_ls_m2_20 * 1000 // 8
        reported_ls_30 = cmd_line_ls_30 // 8
        reported_ls_5000 = cmd_line_ls_m2_5000 * 1000 // 8
        classes = (
            {'kind': 'hfsc', 'root': True, 'handle': '1:'},
            {
                'kind': 'hfsc',
                'handle': '1:10',
                'parent': '1:',
                'leaf': '10:',
                'hfsc': {
                    'ls': {'m1': 0, 'd': 0, 'm2': reported_ls_10},
                    'rt': {'m1': 0, 'd': 0, 'm2': reported_ls_10},
                },
            },
            {
                'kind': 'hfsc',
                'handle': '1:20',
                'parent': '1:',
                'leaf': '20:',
                'hfsc': {
                    'ls': {
                        'm1': reported_ls_m1_20,
                        'd': reported_ls_d_20,
                        'm2': reported_ls_m2_20,
                    },
                    'ul': {'m1': 0, 'd': 0, 'm2': 30000 * 1000},
                },
            },
            {
                'kind': 'hfsc',
                'handle': '1:30',
                'parent': '1:',
                'leaf': '40:',
                'hfsc': {
                    'ls': {'m1': 0, 'd': 0, 'm2': reported_ls_30},
                    'rt': {'m1': 0, 'd': 0, 'm2': reported_ls_30},
                },
            },
            {
                'kind': 'hfsc',
                'handle': '1:5000',
                'parent': '1:',
                'leaf': '5000:',
                'hfsc': {'ls': {'m1': 0, 'd': 0, 'm2': reported_ls_5000}},
            },
        )
        for parsed, correct in zip_longest(
            tc.classes(None, out=data), classes
        ):
            assert parsed == correct
