#
# Copyright 2015-2017 Red Hat, Inc.
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

import marshal
from testlib import VdsmTestCase
from testlib import expandPermutations, permutations
from vdsm.common.compat import pickle, json

from vdsm.common.password import (
    ProtectedPassword,
    protect_passwords,
    unprotect_passwords,
)


class ProtectedPasswordTests(VdsmTestCase):

    def test_str(self):
        p = ProtectedPassword("12345678")
        self.assertNotIn("12345678", str(p))

    def test_repr(self):
        p = ProtectedPassword("12345678")
        self.assertNotIn("12345678", repr(p))

    def test_value(self):
        p = ProtectedPassword("12345678")
        self.assertEqual("12345678", p.value)

    def test_eq(self):
        p1 = ProtectedPassword("12345678")
        p2 = ProtectedPassword("12345678")
        self.assertEqual(p1, p2)

    def test_ne(self):
        p1 = ProtectedPassword("12345678")
        p2 = ProtectedPassword("12345678")
        self.assertFalse(p1 != p2)

    def test_pickle_copy(self):
        p1 = ProtectedPassword("12345678")
        p2 = pickle.loads(pickle.dumps(p1))
        self.assertEqual(p1, p2)

    def test_no_marshal(self):
        p1 = ProtectedPassword("12345678")
        self.assertRaises(ValueError, marshal.dumps, p1)

    def test_no_json(self):
        p1 = ProtectedPassword("12345678")
        self.assertRaises(TypeError, json.dumps, p1)


@expandPermutations
class ProtectTests(VdsmTestCase):

    @permutations([[list()], [dict()], [tuple()]])
    def test_protect_empty(self, params):
        self.assertEqual(params, protect_passwords(params))

    @permutations([[list()], [dict()], [tuple()]])
    def test_unprotect_empty(self, result):
        self.assertEqual(result, unprotect_passwords(result))

    def test_protect_dict(self):
        unprotected = dict_unprotedted()
        protected = dict_protected()
        self.assertEqual(protected, protect_passwords(unprotected))

    def test_unprotect_dict(self):
        protected = dict_protected()
        unprotected = dict_unprotedted()
        self.assertEqual(unprotected, unprotect_passwords(protected))

    def test_protect_nested_dicts(self):
        unprotected = nested_dicts_unprotected()
        protected = nested_dicts_protected()
        self.assertEqual(protected, protect_passwords(unprotected))

    def test_unprotect_nested_dicts(self):
        protected = nested_dicts_protected()
        unprotected = nested_dicts_unprotected()
        self.assertEqual(unprotected, unprotect_passwords(protected))

    def test_protect_lists_of_dicts(self):
        unprotected = lists_of_dicts_unprotected()
        protected = lists_of_dicts_protected()
        self.assertEqual(protected, protect_passwords(unprotected))

    def test_unprotect_lists_of_dicts(self):
        protected = lists_of_dicts_protected()
        unprotected = lists_of_dicts_unprotected()
        self.assertEqual(unprotected, unprotect_passwords(protected))

    def test_protect_nested_lists_of_dicts(self):
        unprotected = nested_lists_of_dicts_unprotected()
        protected = nested_lists_of_dicts_protected()
        self.assertEqual(protected, protect_passwords(unprotected))

    def test_unprotect_nested_lists_of_dicts(self):
        protected = nested_lists_of_dicts_protected()
        unprotected = nested_lists_of_dicts_unprotected()
        self.assertEqual(unprotected, unprotect_passwords(protected))


def dict_unprotedted():
    return {
        "key": "value",
        "password": "12345678"
    }


def dict_protected():
    return {
        "key": "value",
        "password": ProtectedPassword("12345678")
    }


def nested_dicts_unprotected():
    return {
        "key": "value",
        "nested": {
            "password": "12345678",
            "nested": {
                "key": "value",
                "password": "87654321",
            }
        }
    }


def nested_dicts_protected():
    return {
        "key": "value",
        "nested": {
            "password": ProtectedPassword("12345678"),
            "nested": {
                "key": "value",
                "password": ProtectedPassword("87654321"),
            }
        }
    }


def lists_of_dicts_unprotected():
    return [
        {
            "key": "value",
            "password": "12345678",
        },
        {
            "key": "value",
            "password": "87654321",
        }
    ]


def lists_of_dicts_protected():
    return [
        {
            "key": "value",
            "password": ProtectedPassword("12345678"),
        },
        {
            "key": "value",
            "password": ProtectedPassword("87654321"),
        }
    ]


def nested_lists_of_dicts_unprotected():
    return {
        "key": "value",
        "nested": [
            {
                "key": "value",
                "nested": [
                    {
                        "key": "value",
                        "password": "12345678",
                    }
                ]
            }
        ]
    }


def nested_lists_of_dicts_protected():
    return {
        "key": "value",
        "nested": [
            {
                "key": "value",
                "nested": [
                    {
                        "key": "value",
                        "password": ProtectedPassword("12345678"),
                    }
                ]
            }
        ]
    }
