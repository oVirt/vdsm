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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import base64
from testlib import VdsmTestCase
from vdsm import properties
from vdsm.common.password import ProtectedPassword


class PropertyTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Property()

    def test_default(self):
        obj = self.Cls()
        self.assertEqual(obj.value, None)

    def test_set(self):
        obj = self.Cls()
        obj.value = "value"
        self.assertEqual(obj.value, "value")

    def test_value_per_object(self):
        obj1 = self.Cls()
        obj2 = self.Cls()
        obj1.value = "1"
        obj2.value = "2"
        self.assertNotEqual(obj1.value, obj2.value)


class PropertyDocTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Property(doc="description")

    def test_doc(self):
        self.Cls()
        self.assertEqual(self.Cls.value.__doc__, "description")


class PropertyRequiredTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Property(required=True)

    def test_required(self):
        self.assertRaises(ValueError, self.Cls)


class PropertyRequiredNoneTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Property(required=True)

        def __init__(self, value=None):
            self.value = value

    def test_required(self):
        # Required property set to None is considered unset
        self.assertRaises(ValueError, self.Cls)

    def test_assign_raises(self):
        obj = self.Cls(42)
        self.assertRaises(ValueError, setattr, obj, 'value', None)


class PropertyDefaultTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Property(default="default")

    def test_default(self):
        obj = self.Cls()
        self.assertEqual("default", obj.value)


class PropertyDefaultRequiredTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Property(required=True, default="default")

    def test_required(self):
        self.assertRaises(ValueError, self.Cls)


class EnumDefaultTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Enum(values=("1", "2", "3"), default="2")

    def test_invalid_default(self):
        def invalid_default():
            class Cls(properties.Owner):
                value = properties.Enum(values=("1", "2", "3"), default="4")
        self.assertRaises(ValueError, invalid_default)

    def test_default(self):
        obj = self.Cls()
        self.assertEqual(obj.value, "2")

    def test_allowed(self):
        for value in ("1", "2", "3"):
            obj = self.Cls()
            obj.value = value
            self.assertEqual(obj.value, value)

    def test_forbidden(self):
        obj = self.Cls()
        self.assertRaises(ValueError, setattr, obj, "value", "4")

    def test_none(self):
        obj = self.Cls()
        obj.value = None
        self.assertEqual(None, obj.value)


class EnumMixedTypesTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Enum(values=(1, "2", 3.0), default="2")

    def test_types(self):
        # This is bad idea, but we don't forbidd this for now.
        obj = self.Cls()
        obj.value = 1
        obj.value = "2"
        obj.value = 3.0


class EnumRequiredTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Enum(required=True)

        def __init__(self, value=None):
            self.value = value

    def test_required(self):
        self.assertRaises(ValueError, self.Cls)


class StringTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.String()

    def test_default(self):
        obj = self.Cls()
        self.assertEqual(None, obj.value)

    def test_str(self):
        obj = self.Cls()
        obj.value = "value"
        self.assertEqual(obj.value, "value")

    def test_unicode(self):
        obj = self.Cls()
        obj.value = u"\u05d0"  # Alef
        self.assertEqual(obj.value, u"\u05d0")

    def test_invalid(self):
        obj = self.Cls()
        self.assertRaises(ValueError, setattr, obj, "value", 1)

    def test_none(self):
        obj = self.Cls()
        obj.value = None
        self.assertEqual(None, obj.value)


class StringRequiredTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.String(required=True)

        def __init__(self, value=None):
            self.value = value

    def test_required(self):
        self.assertRaises(ValueError, self.Cls)

    def test_empty(self):
        obj = self.Cls("")
        self.assertEqual("", obj.value)


class IntegerTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Integer()

    def test_default(self):
        obj = self.Cls()
        self.assertEqual(None, obj.value)

    def test_valid(self):
        obj = self.Cls()
        obj.value = 7
        self.assertEqual(7, obj.value)

    def test_invalid(self):
        obj = self.Cls()
        self.assertRaises(ValueError, setattr, obj, "value", 3.14)

    def test_none(self):
        obj = self.Cls()
        obj.value = None
        self.assertEqual(None, obj.value)


class IntegerMinValueTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Integer(minval=0)

    def test_valid(self):
        obj = self.Cls()
        obj.value = 7
        self.assertEqual(7, obj.value)

    def test_too_small(self):
        obj = self.Cls()
        self.assertRaises(ValueError, setattr, obj, "value", -1)


class IntegerMaxValueTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Integer(maxval=100)

    def test_valid(self):
        obj = self.Cls()
        obj.value = 7
        self.assertEqual(7, obj.value)

    def test_too_large(self):
        obj = self.Cls()
        self.assertRaises(ValueError, setattr, obj, "value", 101)


class IntegerRequiredTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Integer(required=True)

        def __init__(self, value=None):
            self.value = value

    def test_required(self):
        self.assertRaises(ValueError, self.Cls)

    def test_zero(self):
        obj = self.Cls(0)
        self.assertEqual(0, obj.value)


class FloatTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Float()

    def test_default(self):
        obj = self.Cls()
        self.assertEqual(None, obj.value)

    def test_valid(self):
        obj = self.Cls()
        obj.value = 3.14
        self.assertEqual(obj.value, 3.14)

    def test_invalid(self):
        obj = self.Cls()
        self.assertRaises(ValueError, setattr, obj, "value", "not a float")

    def test_int(self):
        obj = self.Cls()
        self.assertRaises(ValueError, setattr, obj, "value", 3)

    def test_none(self):
        obj = self.Cls()
        obj.value = None
        self.assertEqual(None, obj.value)


class FloatMinValueTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Float(minval=1.0)

    def test_valid(self):
        obj = self.Cls()
        obj.value = 1.0
        self.assertEqual(1.0, obj.value)

    def test_too_small(self):
        obj = self.Cls()
        self.assertRaises(ValueError, setattr, obj, "value", 0.999999999)


class FloatMaxValueTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Float(maxval=1.0)

    def test_valid(self):
        obj = self.Cls()
        obj.value = 0.2
        self.assertEqual(0.2, obj.value)

    def test_too_large(self):
        obj = self.Cls()
        self.assertRaises(ValueError, setattr, obj, "value", 1.000000001)


class FloatRequiredTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Float(required=True)

        def __init__(self, value=None):
            self.value = value

    def test_required(self):
        self.assertRaises(ValueError, self.Cls)

    def test_zero(self):
        obj = self.Cls(0.0)
        self.assertEqual(0.0, obj.value)


class BooleanTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Boolean()

    def test_not_set(self):
        obj = self.Cls()
        self.assertEqual(None, obj.value)

    def test_true(self):
        obj = self.Cls()
        obj.value = True
        self.assertEqual(True, obj.value)

    def test_false(self):
        obj = self.Cls()
        obj.value = False
        self.assertEqual(False, obj.value)

    def test_invalid(self):
        obj = self.Cls()
        self.assertRaises(ValueError, setattr, obj, "value", "not a bool")

    def test_none(self):
        obj = self.Cls()
        obj.value = None
        self.assertEqual(None, obj.value)


class BooleanDefaultTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Boolean(default=False)

    def test_default(self):
        obj = self.Cls()
        self.assertEqual(False, obj.value)


class BooleanRequiredTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.Boolean(required=True)

    def test_required(self):
        self.assertRaises(ValueError, self.Cls)


class UUIDTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.UUID(default="00000000-0000-0000-0000-000000000000")

    def test_default(self):
        obj = self.Cls()
        self.assertEqual("00000000-0000-0000-0000-000000000000", obj.value)

    def test_valid(self):
        obj = self.Cls()
        obj.value = "774229a2-9300-474d-9d95-ab8423df94e1"
        self.assertEqual("774229a2-9300-474d-9d95-ab8423df94e1", obj.value)

    def test_invalid(self):
        obj = self.Cls()
        self.assertRaises(ValueError, setattr, obj, "value", "not-a-uuid")

    def test_none(self):
        obj = self.Cls()
        obj.value = None
        self.assertEqual(None, obj.value)


class UUIDRequiredTests(VdsmTestCase):

    class Cls(properties.Owner):
        value = properties.UUID(required=True)

    def test_required(self):
        self.assertRaises(ValueError, self.Cls)


class PasswordTests(VdsmTestCase):

    class Cls(properties.Owner):
        password = properties.Password()

    def test_default(self):
        obj = self.Cls()
        self.assertEqual(None, obj.password)

    def test_valid(self):
        obj = self.Cls()
        obj.password = ProtectedPassword("12345678")
        self.assertEqual("12345678", obj.password.value)

    def test_invalid(self):
        obj = self.Cls()
        self.assertRaises(ValueError, setattr, obj, "password",
                          "bare password")

    def test_none(self):
        obj = self.Cls()
        obj.password = None
        self.assertEqual(None, obj.password)


class PasswordRequiredTests(VdsmTestCase):

    class Cls(properties.Owner):
        password = properties.Password(required=True)

        def __init__(self, password=None):
            self.password = password

    def test_required(self):
        self.assertRaises(ValueError, self.Cls)


class PasswordDecodeTests(VdsmTestCase):

    class Cls(properties.Owner):
        password = properties.Password(decode=properties.decode_base64)

    def test_decode(self):
        obj = self.Cls()
        data = b"\x80\x81\x82\x83"
        obj.password = ProtectedPassword(base64.b64encode(data))
        self.assertEqual(data, obj.password.value)

    def test_invalid(self):
        obj = self.Cls()
        self.assertRaises(ValueError, setattr, obj, "password",
                          ProtectedPassword("not base64 value"))
