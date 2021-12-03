#
# Copyright 2015-2021 Red Hat, Inc.
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

import marshal
import pickle
import pytest

from testlib import VdsmTestCase
from vdsm.common.compat import json

from vdsm.common.password import (
    HiddenValue,
    protect_passwords,
    unhide,
)


class HiddenValueTests(VdsmTestCase):

    def test_str(self):
        p = HiddenValue("12345678")
        self.assertNotIn("12345678", str(p))

    def test_repr(self):
        p = HiddenValue("12345678")
        self.assertNotIn("12345678", repr(p))

    def test_value(self):
        p = HiddenValue("12345678")
        self.assertEqual("12345678", p.value)

    def test_eq(self):
        p1 = HiddenValue("12345678")
        p2 = HiddenValue("12345678")
        self.assertEqual(p1, p2)

    def test_ne(self):
        p1 = HiddenValue("12345678")
        p2 = HiddenValue("12345678")
        self.assertFalse(p1 != p2)

    def test_pickle_copy(self):
        p1 = HiddenValue("12345678")
        p2 = pickle.loads(pickle.dumps(p1))
        self.assertEqual(p1, p2)

    def test_no_marshal(self):
        p1 = HiddenValue("12345678")
        self.assertRaises(ValueError, marshal.dumps, p1)

    def test_no_json(self):
        p1 = HiddenValue("12345678")
        self.assertRaises(TypeError, json.dumps, p1)


def dict_unprotected():
    return {
        "key": "value",
        "_X_key": "secret",
        "password": "12345678"
    }


def dict_protected():
    return {
        "key": "value",
        "_X_key": HiddenValue("secret"),
        "password": HiddenValue("12345678")
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
        "_X_key": HiddenValue("secret"),
        "nested": {
            "password": HiddenValue("12345678"),
            "nested": {
                "key": "value",
                "_X_key": HiddenValue("secret"),
                "password": HiddenValue("87654321"),
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
            "_X_key": HiddenValue("secret"),
            "password": HiddenValue("12345678"),
        },
        {
            "key": "value",
            "_X_key": HiddenValue("secret"),
            "password": HiddenValue("87654321"),
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
                        "_X_key": HiddenValue("secret"),
                        "password": HiddenValue("12345678"),
                    }
                ]
            }
        ]
    }


def hidden_inside_hidden_unprotected():
    return {
        "key": "value",
        "nested": [
            {
                "key": "value",
                "nested": [
                    {
                        "key": "value",
                        "secret": "12345678",
                    }
                ]
            }
        ]
    }


def hidden_inside_hidden_protected():
    return {
        "key": "value",
        "nested": HiddenValue([
            {
                "key": "value",
                "nested": [
                    {
                        "key": "value",
                        "secret": HiddenValue("12345678"),
                    }
                ]
            }
        ])
    }


class ProtectTests:

    @pytest.mark.parametrize("param", [[list()], [dict()], [tuple()]])
    def test_protect_empty(self, param):
        assert param == protect_passwords(param)

    @pytest.mark.parametrize("result", [[list()], [dict()], [tuple()]])
    def test_unprotect_empty(self, result):
        assert result == unhide(result)

    @pytest.mark.parametrize("protected,unprotected", [
        (dict_protected, dict_unprotected),
        (nested_dicts_protected, nested_dicts_unprotected),
        (lists_of_dicts_protected, lists_of_dicts_unprotected),
        (nested_lists_of_dicts_protected, nested_lists_of_dicts_unprotected),
    ])
    def test_protect(self, protected, unprotected):
        protected_value = protected()
        unprotected_value = unprotected()
        assert protected_value == protect_passwords(unprotected_value)

    @pytest.mark.parametrize("protected,unprotected", [
        (dict_protected, dict_unprotected),
        (nested_dicts_protected, nested_dicts_unprotected),
        (lists_of_dicts_protected, lists_of_dicts_unprotected),
        (nested_lists_of_dicts_protected, nested_lists_of_dicts_unprotected),
        (hidden_inside_hidden_protected, hidden_inside_hidden_unprotected),
    ])
    def test_unprotect(self, protected, unprotected):
        protected_value = protected()
        unprotected_value = unprotected()
        assert unprotected_value == unhide(protected_value)
        assert protected_value == protected()
