#
# Copyright 2012 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
import logging
from collections import namedtuple
import guestIF

from testrunner import VdsmTestCase as TestCaseBase


class TestGuestIF(TestCaseBase):

    def testfilterXmlChars(self):
        ALL_LEGAL = "Hello World"
        self.assertEqual(ALL_LEGAL, guestIF._filterXmlChars(ALL_LEGAL))
        TM = u"\u2122".encode('utf8')
        self.assertNotEqual(TM, guestIF._filterXmlChars(TM))

    def test_handleMessage(self):
        logging.TRACE = 5
        fakeGuestAgent = guestIF.GuestAgent(None,
                                            None, self.log, connect=False)
        testCase = namedtuple('testCase', 'msgType, message, assertDict')

        msgTypes = ['heartbeat', 'host-name', 'os-version',
                    'network-interfaces', 'applications', 'disks-usage']

        inputs = [
            {'free-ram': 1024000,
             'memory-stat': {'swap_out': 0, 'majflt': 0, 'mem_free': 4466104,
                             'swap_in': 0, 'pageflt': 0, 'mem_total': 8059320,
                             'mem_unused': 2733832}},

            {'name': 'example.ovirt.org'},
            {'version': '2.6.32-71.el6.x86_64'},

            {'interfaces':[
                {'hw': '00:21:cc:68:d7:38', 'name': 'eth0', 'inet':
                 ['9.115.122.77'], 'inet6': ['fe80::221:ccff:fe68:d738']},
                {'hw': 'a0:88:b4:f0:ce:a0', 'name': 'wlan0', 'inet':
                 ['9.115.126.23'], 'inet6': ['fe80::a288:b4ff:fef0:cea0']},
                {'hw': '52:54:00:5b:3f:e1', 'name': 'virbr0', 'inet':
                 ['192.168.122.1'], 'inet6': []}]},

            {'applications':
                ['kernel-2.6.32-71.7.1.el6', 'kernel-2.6.32-220.el6']},

            {'disks':[
                {'total': 130062397440, 'path': '/', 'fs': 'ext4',
                 'used':76402614272},
                {'total': 203097088, 'path': '/boot', 'fs': 'ext4',
                 'used': 153149440}]}]

        outputs = [
            {'memUsage': 1024000, 'memoryStats':
                {'swap_out': '0', 'majflt': '0', 'mem_free':
                 '4466104', 'swap_in': '0', 'pageflt': '0',
                 'mem_total': '8059320', 'mem_unused': '2733832'}},

            {'guestName': 'example.ovirt.org'},
            {'guestOs': '2.6.32-71.el6.x86_64'},

            {'netIfaces': [
                {'hw': '00:21:cc:68:d7:38', 'name': 'eth0', 'inet':
                 ['9.115.122.77'], 'inet6': ['fe80::221:ccff:fe68:d738']},
                {'hw': 'a0:88:b4:f0:ce:a0', 'name': 'wlan0', 'inet':
                 ['9.115.126.23'], 'inet6': ['fe80::a288:b4ff:fef0:cea0']},
                {'hw': '52:54:00:5b:3f:e1', 'name': 'virbr0', 'inet':
                 ['192.168.122.1'], 'inet6': []}],
             'guestIPs': '9.115.122.77 9.115.126.23 192.168.122.1'},

            {'appsList':
                ['kernel-2.6.32-71.7.1.el6', 'kernel-2.6.32-220.el6']},

            {'disksUsage': [
                {'total': '130062397440', 'path': '/', 'fs': 'ext4',
                 'used': '76402614272'},
                {'total': '203097088', 'path': '/boot', 'fs': 'ext4',
                 'used': '153149440'}]}]

        for t in zip(msgTypes, inputs, outputs):
            t = testCase(*t)
            fakeGuestAgent._handleMessage(t.msgType, t.message)
            for (k, v) in t.assertDict.iteritems():
                self.assertEqual(fakeGuestAgent.guestInfo[k], v)
