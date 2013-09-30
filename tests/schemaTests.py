# Copyright 2012 IBM Corporation.
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

from testrunner import VdsmTestCase as TestCaseBase
import vdsmapi


class SchemaTest(TestCaseBase):

    def testSchemaParse(self):
        self.assertTrue(isinstance(vdsmapi.get_api(), dict))

    ## Supported JSON syntax

    def testTokenizeEmpty(self):
        tokens = list(vdsmapi.tokenize(''))
        self.assertEqual(tokens, [])

    def testTokenizeString(self):
        tokens = list(vdsmapi.tokenize("'string'"))
        self.assertEqual(tokens, ['string'])

    def testTokenizeStringWithWhitespace(self):
        tokens = list(vdsmapi.tokenize("'s1 s2'"))
        self.assertEqual(tokens, ['s1 s2'])

    def testTokenizeStringEmpty(self):
        tokens = list(vdsmapi.tokenize("''"))
        self.assertEqual(tokens, [''])

    def testTokenizeArray(self):
        tokens = list(vdsmapi.tokenize("['i1', 'i2']"))
        self.assertEqual(tokens, ['[', 'i1', ',', 'i2', ']'])

    def testTokenizeArrayEmpty(self):
        tokens = list(vdsmapi.tokenize("[]"))
        self.assertEqual(tokens, ['[', ']'])

    def testTokenizeObject(self):
        tokens = list(vdsmapi.tokenize("{'a': 'b', 'c': 'd'}"))
        self.assertEqual(tokens, ['{', 'a', ':', 'b', ',', 'c', ':', 'd', '}'])

    def testTokenizeObjectEmpty(self):
        tokens = list(vdsmapi.tokenize("{}"))
        self.assertEqual(tokens, ['{', '}'])

    def testTokenizeMixed(self):
        tokens = list(vdsmapi.tokenize("{'a': {'b': ['c']}}"))
        self.assertEqual(tokens, ['{', 'a', ':', '{', 'b', ':', '[', 'c', ']',
                         '}', '}'])

    def testTokenizeSkipWhitespaceBetweenTokens(self):
        tokens = list(vdsmapi.tokenize(" { 'a': \n 'b' , 'c'\n\n : 'd' } \n"))
        self.assertEqual(tokens, ['{', 'a', ':', 'b', ',', 'c', ':', 'd', '}'])

    ## Unsupported JSON syntax

    def testTokenizeRaiseOnNumber(self):
        generator = vdsmapi.tokenize("1")
        self.assertRaises(ValueError, list, generator)

    def testTokenizeRaiseOnTrue(self):
        generator = vdsmapi.tokenize("true")
        self.assertRaises(ValueError, list, generator)

    def testTokenizeRaiseOnFalse(self):
        generator = vdsmapi.tokenize("false")
        self.assertRaises(ValueError, list, generator)

    def testTokenizeRaiseOnNull(self):
        generator = vdsmapi.tokenize("null")
        self.assertRaises(ValueError, list, generator)

    ## Invalid JSON

    def testTokenizeRaiseOnInvalidData(self):
        generator = vdsmapi.tokenize("{'a': invalid, 'b': 'c'}")
        self.assertRaises(ValueError, list, generator)
