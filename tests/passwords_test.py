# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import json
import marshal
import pickle

from testlib import VdsmTestCase
from testlib import expandPermutations, permutations

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
        unprotected = dict_unprotected()
        protected = dict_protected()
        self.assertEqual(protected, protect_passwords(unprotected))

    def test_unprotect_dict(self):
        protected = dict_protected()
        unprotected = dict_unprotected()
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


def dict_unprotected():
    return {
        "key": "value",
        "_X_key": "secret",
        "password": "12345678"
    }


def dict_protected():
    return {
        "key": "value",
        "_X_key": ProtectedPassword("secret"),
        "password": ProtectedPassword("12345678")
    }


def nested_dicts_unprotected():
    return {
        "key": "value",
        "_X_key": "secret",
        "nested": {
            "password": "12345678",
            "nested": {
                "key": "value",
                "_X_key": "secret",
                "password": "87654321",
            }
        }
    }


def nested_dicts_protected():
    return {
        "key": "value",
        "_X_key": ProtectedPassword("secret"),
        "nested": {
            "password": ProtectedPassword("12345678"),
            "nested": {
                "key": "value",
                "_X_key": ProtectedPassword("secret"),
                "password": ProtectedPassword("87654321"),
            }
        }
    }


def lists_of_dicts_unprotected():
    return [
        {
            "key": "value",
            "_X_key": "secret",
            "password": "12345678",
        },
        {
            "key": "value",
            "_X_key": "secret",
            "password": "87654321",
        }
    ]


def lists_of_dicts_protected():
    return [
        {
            "key": "value",
            "_X_key": ProtectedPassword("secret"),
            "password": ProtectedPassword("12345678"),
        },
        {
            "key": "value",
            "_X_key": ProtectedPassword("secret"),
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
                        "_X_key": "secret",
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
                        "_X_key": ProtectedPassword("secret"),
                        "password": ProtectedPassword("12345678"),
                    }
                ]
            }
        ]
    }
