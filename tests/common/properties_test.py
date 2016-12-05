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

import pytest

from vdsm.common import properties
from vdsm.common.password import ProtectedPassword


class TestProperty:

    class Cls(properties.Owner):
        value = properties.Property()

    def test_default(self):
        obj = self.Cls()
        assert obj.value is None

    def test_set(self):
        obj = self.Cls()
        obj.value = "value"
        assert obj.value == "value"

    def test_value_per_object(self):
        obj1 = self.Cls()
        obj2 = self.Cls()
        obj1.value = "1"
        obj2.value = "2"
        assert obj1.value != obj2.value


class TestPropertyDoc:

    class Cls(properties.Owner):
        value = properties.Property(doc="description")

    def test_doc(self):
        self.Cls()
        assert self.Cls.value.__doc__ == "description"


class TestPropertyRequired:

    class Cls(properties.Owner):
        value = properties.Property(required=True)

    def test_required(self):
        with pytest.raises(ValueError):
            self.Cls()


class TestPropertyRequiredNone:

    class Cls(properties.Owner):
        value = properties.Property(required=True)

        def __init__(self, value=None):
            self.value = value

    def test_required(self):
        # Required property set to None is considered unset
        with pytest.raises(ValueError):
            self.Cls()

    def test_assign_raises(self):
        obj = self.Cls(42)
        with pytest.raises(ValueError):
            obj.value = None


class TestPropertyDefault:

    class Cls(properties.Owner):
        value = properties.Property(default="default")

    def test_default(self):
        obj = self.Cls()
        assert obj.value == "default"


class TestPropertyDefaultRequired:

    class Cls(properties.Owner):
        value = properties.Property(required=True, default="default")

    def test_required(self):
        with pytest.raises(ValueError):
            self.Cls()


class TestEnumDefault:

    class Cls(properties.Owner):
        value = properties.Enum(values=("1", "2", "3"), default="2")

    def test_invalid_default(self):
        def invalid_default():
            class Cls(properties.Owner):
                value = properties.Enum(values=("1", "2", "3"), default="4")
        with pytest.raises(ValueError):
            invalid_default()

    def test_default(self):
        obj = self.Cls()
        assert obj.value == "2"

    def test_allowed(self):
        for value in ("1", "2", "3"):
            obj = self.Cls()
            obj.value = value
            assert obj.value == value

    def test_forbidden(self):
        obj = self.Cls()
        with pytest.raises(ValueError):
            obj.value = "4"

    def test_none(self):
        obj = self.Cls()
        obj.value = None
        assert obj.value is None


class TestEnumMixedTypes:

    class Cls(properties.Owner):
        value = properties.Enum(values=(1, "2", 3.0), default="2")

    def test_types(self):
        # This is bad idea, but we don't forbidd this for now.
        obj = self.Cls()
        obj.value = 1
        obj.value = "2"
        obj.value = 3.0


class TestEnumRequired:

    class Cls(properties.Owner):
        value = properties.Enum(required=True)

        def __init__(self, value=None):
            self.value = value

    def test_required(self):
        with pytest.raises(ValueError):
            self.Cls()


class TestString:

    class Cls(properties.Owner):
        value = properties.String()

    def test_default(self):
        obj = self.Cls()
        assert obj.value is None

    def test_str(self):
        obj = self.Cls()
        obj.value = "value"
        assert obj.value == "value"

    def test_unicode(self):
        obj = self.Cls()
        obj.value = "\u05d0"  # Alef
        assert obj.value == "\u05d0"

    def test_invalid(self):
        obj = self.Cls()
        with pytest.raises(ValueError):
            obj.value = 1

    def test_none(self):
        obj = self.Cls()
        obj.value = None
        assert obj.value is None


class TestStringRequired:

    class Cls(properties.Owner):
        value = properties.String(required=True)

        def __init__(self, value=None):
            self.value = value

    def test_required(self):
        with pytest.raises(ValueError):
            self.Cls()

    def test_empty(self):
        obj = self.Cls("")
        assert obj.value == ""


class TestInteger:

    class Cls(properties.Owner):
        value = properties.Integer()

    def test_default(self):
        obj = self.Cls()
        assert obj.value is None

    def test_valid(self):
        obj = self.Cls()
        obj.value = 7
        assert obj.value == 7

    def test_invalid(self):
        obj = self.Cls()
        with pytest.raises(ValueError):
            obj.value = 3.14

    def test_none(self):
        obj = self.Cls()
        obj.value = None
        assert obj.value is None


class TestIntegerMinValue:

    class Cls(properties.Owner):
        value = properties.Integer(minval=0)

    def test_valid(self):
        obj = self.Cls()
        obj.value = 7
        assert obj.value == 7

    def test_too_small(self):
        obj = self.Cls()
        with pytest.raises(ValueError):
            obj.value = -1


class TestIntegerMaxValue:

    class Cls(properties.Owner):
        value = properties.Integer(maxval=100)

    def test_valid(self):
        obj = self.Cls()
        obj.value = 7
        assert obj.value == 7

    def test_too_large(self):
        obj = self.Cls()
        with pytest.raises(ValueError):
            obj.value = 101


class TestIntegerRequired:

    class Cls(properties.Owner):
        value = properties.Integer(required=True)

        def __init__(self, value=None):
            self.value = value

    def test_required(self):
        with pytest.raises(ValueError):
            self.Cls()

    def test_zero(self):
        obj = self.Cls(0)
        assert obj.value == 0


class TestFloat:

    class Cls(properties.Owner):
        value = properties.Float()

    def test_default(self):
        obj = self.Cls()
        assert obj.value is None

    def test_valid(self):
        obj = self.Cls()
        obj.value = 3.14
        assert obj.value == 3.14

    def test_invalid(self):
        obj = self.Cls()
        with pytest.raises(ValueError):
            obj.value = "not a float"

    def test_int(self):
        obj = self.Cls()
        with pytest.raises(ValueError):
            obj.value = 3

    def test_none(self):
        obj = self.Cls()
        obj.value = None
        assert obj.value is None


class TestFloatMinValue:

    class Cls(properties.Owner):
        value = properties.Float(minval=1.0)

    def test_valid(self):
        obj = self.Cls()
        obj.value = 1.0
        assert obj.value == 1.0

    def test_too_small(self):
        obj = self.Cls()
        with pytest.raises(ValueError):
            obj.value = 0.999999999


class TestFloatMaxValue:

    class Cls(properties.Owner):
        value = properties.Float(maxval=1.0)

    def test_valid(self):
        obj = self.Cls()
        obj.value = 0.2
        assert obj.value == 0.2

    def test_too_large(self):
        obj = self.Cls()
        with pytest.raises(ValueError):
            obj.value = 1.000000001


class TestFloatRequired:

    class Cls(properties.Owner):
        value = properties.Float(required=True)

        def __init__(self, value=None):
            self.value = value

    def test_required(self):
        with pytest.raises(ValueError):
            self.Cls()

    def test_zero(self):
        obj = self.Cls(0.0)
        assert obj.value == 0.0


class TestBoolean:

    class Cls(properties.Owner):
        value = properties.Boolean()

    def test_not_set(self):
        obj = self.Cls()
        assert obj.value is None

    def test_true(self):
        obj = self.Cls()
        obj.value = True
        assert obj.value is True

    def test_false(self):
        obj = self.Cls()
        obj.value = False
        assert obj.value is False

    def test_invalid(self):
        obj = self.Cls()
        with pytest.raises(ValueError):
            obj.value = "not a bool"

    def test_none(self):
        obj = self.Cls()
        obj.value = None
        assert obj.value is None


class TestBooleanDefault:

    class Cls(properties.Owner):
        value = properties.Boolean(default=False)

    def test_default(self):
        obj = self.Cls()
        assert obj.value is False


class TestBooleanRequired:

    class Cls(properties.Owner):
        value = properties.Boolean(required=True)

    def test_required(self):
        with pytest.raises(ValueError):
            self.Cls()


class TestUUID:

    class Cls(properties.Owner):
        value = properties.UUID(default="00000000-0000-0000-0000-000000000000")

    def test_default(self):
        obj = self.Cls()
        assert obj.value == "00000000-0000-0000-0000-000000000000"

    def test_valid(self):
        obj = self.Cls()
        obj.value = "774229a2-9300-474d-9d95-ab8423df94e1"
        assert obj.value == "774229a2-9300-474d-9d95-ab8423df94e1"

    def test_invalid(self):
        obj = self.Cls()
        with pytest.raises(ValueError):
            obj.value = "not-a-uuid"

    def test_none(self):
        obj = self.Cls()
        obj.value = None
        assert obj.value is None


class TestUUIDRequired:

    class Cls(properties.Owner):
        value = properties.UUID(required=True)

    def test_required(self):
        with pytest.raises(ValueError):
            self.Cls()


class TestPassword:

    class Cls(properties.Owner):
        password = properties.Password()

    def test_default(self):
        obj = self.Cls()
        assert obj.password is None

    def test_valid(self):
        obj = self.Cls()
        obj.password = ProtectedPassword("12345678")
        assert obj.password.value == "12345678"

    def test_invalid(self):
        obj = self.Cls()
        with pytest.raises(ValueError):
            obj.password = "bare password"

    def test_none(self):
        obj = self.Cls()
        obj.password = None
        assert obj.password is None


class TestPasswordRequired:

    class Cls(properties.Owner):
        password = properties.Password(required=True)

        def __init__(self, password=None):
            self.password = password

    def test_required(self):
        with pytest.raises(ValueError):
            self.Cls()


class TestPasswordDecode:

    class Cls(properties.Owner):
        password = properties.Password(decode=properties.decode_base64)

    def test_decode(self):
        obj = self.Cls()
        data = b"\x80\x81\x82\x83"
        obj.password = ProtectedPassword(base64.b64encode(data))
        assert obj.password.value == data

    def test_invalid(self):
        obj = self.Cls()
        with pytest.raises(ValueError):
            obj.password = ProtectedPassword("not base64 value")
