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

import textwrap
from contextlib import contextmanager

from testlib import temporaryPath
from testlib import VdsmTestCase as TestCaseBase
from api import vdsmapi


class SchemaTest(TestCaseBase):

    def testSchemaParse(self):
        self.assertTrue(isinstance(vdsmapi.get_api(), dict))

    # Supported JSON syntax

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

    # Unsupported JSON syntax

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

    # Invalid JSON

    def testTokenizeRaiseOnInvalidData(self):
        generator = vdsmapi.tokenize("{'a': invalid, 'b': 'c'}")
        self.assertRaises(ValueError, list, generator)


class ParseTest(TestCaseBase):
    blank_schema = {'types': {}, 'enums': {}, 'aliases': {},
                    'maps': {}, 'commands': {}, 'unions': {}}

    @contextmanager
    def load_api(self, data):
        vdsmapi._api_info = None
        data = textwrap.dedent(data)
        with temporaryPath(data=data) as filename:
            yield vdsmapi.get_api(filename)

    def assertTypeRelation(self, api, type_a, type_b):
        self.assertIn(type_b, api['unions'][type_a])
        self.assertIn(type_a, api['unions'][type_b])

    def test_empty_schema(self):
        with self.load_api('') as api:
            self.assertEqual(self.blank_schema, api)

    def test_unknown_symbol_type(self):
        with self.load_api('''
        {'foo': 'bar', 'data': ['a', 'b', 'c']}
        ''') as api:
            self.assertEqual(self.blank_schema, api)

    def test_single_type(self):
        with self.load_api('''
        {'type': 'foo', 'data': {'a': 'str'}}
        ''') as api:
            self.assertIn('foo', api['types'])
            self.assertEqual('str', api['types']['foo']['data']['a'])

    def test_single_enum(self):
        with self.load_api('''
        {'enum': 'foo', 'data': ['a', 'b', 'c']}
        ''') as api:
            self.assertEqual(['a', 'b', 'c'], api['enums']['foo']['data'])

    def test_single_alias(self):
        with self.load_api('''
        {'alias': 'UUID', 'data': 'str'}
        ''') as api:
            self.assertEqual('str', api['aliases']['UUID']['data'])

    def test_single_map(self):
        map = {'map': 'foo', 'key': 'str', 'value': 'str'}
        with self.load_api(str(map)) as api:
            self.assertEqual(map, api['maps']['foo'])

    def test_single_command(self):
        cmd = {'command': {'class': 'foo', 'name': 'bar'},
               'data': {'a': 'uint'}, 'returns': 'str'}
        with self.load_api(str(cmd)) as api:
            self.assertEqual(cmd, api['commands']['foo']['bar'])

    def test_command_parameter_order(self):
        # The parser will preserve the order of dict keys when loading
        with self.load_api('''
        {'command': {'class': 'foo', 'name': 'bar'},
         'data': {'a': 'uint', 'b': 'str', 'c': 'int'}}
        ''') as api:
            self.assertEqual(['a', 'b', 'c'],
                             api['commands']['foo']['bar']['data'].keys())

    def test_type_union(self):
        with self.load_api('''
        {'enum': 'ThingTypes', 'data': ['person', 'place']}

        {'type': 'Thing',
         'data': {'specificType': 'ThingTypes', 'name': 'str'},
         'union': ['Person', 'Place']}

        {'type': 'Person',
         'data': {'specificType': 'ThingTypes', 'name': 'str',
                  'address': 'str'}}

        {'type': 'Place',
         'data': {'specificType': 'ThingTypes', 'name': 'str',
                  'longitude': 'float', 'latitude': 'float'}}
        ''') as api:
            self.assertTypeRelation(api, 'Thing', 'Person')
            self.assertTypeRelation(api, 'Thing', 'Place')
