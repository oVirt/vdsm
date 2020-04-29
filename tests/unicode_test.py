#
# Copyright 2015 Red Hat, Inc.
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

from testlib import VdsmTestCase
from testlib import permutations, expandPermutations
from testValidation import broken_on_ci

ENCODE = [
    # value, encoded (utf8)
    (u'\u05d0', '\xd7\x90'),
    ('\xd7\x90', '\xd7\x90'),
    (u'ascii', 'ascii'),
    ('ascii', 'ascii'),
]

DECODE = [
    # value (utf8), decoded
    ('\xd7\x90', u'\u05d0'),
    (u'\u05d0', u'\u05d0'),
    ('ascii', u'ascii'),
    (u'ascii', u'ascii'),
]

broken_on_travis = broken_on_ci(
    "importing sitecustomize fails", name="TRAVIS_CI")


@expandPermutations
class TestUnicode(VdsmTestCase):

    @permutations(ENCODE)
    @broken_on_travis
    def test_encode(self, value, encoded):
        self.assertEqual(value.encode("utf8"), encoded)

    @permutations(ENCODE)
    @broken_on_travis
    def test_str(self, value, encoded):
        self.assertEqual(str(value), encoded)

    @permutations(DECODE)
    @broken_on_travis
    def test_decode(self, value, decoded):
        self.assertEqual(value.decode("utf8"), decoded)

    @permutations(DECODE)
    @broken_on_travis
    def test_unicode(self, value, decoded):
        self.assertEqual(unicode(value), decoded)

    @broken_on_travis
    def test_mix_add(self):
        self.assertEqual(u'\u05d0' + '\xd7\x91', u'\u05d0\u05d1')

    @broken_on_travis
    def test_mix_format_str(self):
        self.assertEqual(u'\u05d0%s' % '\xd7\x91', u'\u05d0\u05d1')

    @broken_on_travis
    def test_mix_format_unicode(self):
        self.assertEqual('\xd7\x90%s' % u'\u05d1', u'\u05d0\u05d1')
