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
from testlib import (
    VdsmTestCase as TestCaseBase,
    expandPermutations,
    permutations
)
from yajsonrpc.stomp import decodeValue, encodeValue

PERMUTATIONS = (('accept-version',),
                ('1.2:',),
                ('98c592f4-e2e2-46ea-b7b6-aa4f57f924b9\n\r',),
                ('98c592f4\-e2e2-46ea-b7b6-aa4f57f924b9',))


@expandPermutations
class EncodingTests(TestCaseBase):

    @permutations(PERMUTATIONS)
    def test_encoding_value(self, value):
        encoded = encodeValue(value)
        self.assertEqual(value, decodeValue(encoded))
