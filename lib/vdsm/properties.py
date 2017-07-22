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

"""
properties - reusable properties

Properties are reusable objects similar to builtin property() function, adding
input validation and initialization grantees.


This module provides the following properties:

  Boolean       keeps a boolean value
  Enum          keeps one of values from the values list
  Float         keeps a floating point value within limits
  Integer       keeps integer value within limits
  String        keeps string value
  UUID          keeps a UUID string
  Password      keeps a ProtectedPassword object


Common property attributes:

  required      Ensure that a property is initialized in __init__
  default       Default value if property is not set (None)
  doc           Property docstring, accessible via help()


Examples

    class Foo(properties.Owner):

        uuid = properties.UUID(required=True)
        format = properties.Enum(values=("cow", "raw"), required=True)
        size = properties.Integer(minval=0)
        name = properties.String()

        def __init__(self, uuid, format, size=0):
            self.uuid = uuid
            self.format = format
            self.size = size

Note that you must inherit from properties.Owner, which implements the required
magic for naming properties and checking required properties after an object is
created.

Given Foo definition, it cannot be created with missing or invalid properties.
The following expression would raise ValueError:

    Foo("not-a-uuid", "raw")
    Foo("49d8842d-43e8-4c33-b588-b5538df4ed8a", "other")
    Foo("49d8842d-43e8-4c33-b588-b5538df4ed8a", "raw", size=-1)

After creation, you cannot set invalid values:

    f = Foo("49d8842d-43e8-4c33-b588-b5538df4ed8a", "raw")
    f.size = -1  # Will raise ValueError

Note that "name" was not initialized in __init__, but it is defined as
property, so the attribute exists, returning None:

    f = Foo("49d8842d-43e8-4c33-b588-b5538df4ed8a", "raw")
    f.name  # None

"""

from __future__ import absolute_import

import base64
import uuid

import six

from vdsm.common.password import ProtectedPassword


class Property(object):

    def __init__(self, required=False, default=None, doc=None):
        self.name = None
        self.required = required
        self.default = default
        self.__doc__ = doc

    def __get__(self, obj, objtype=None):
        """
        Called when getting a property value.
        """
        if obj is None:
            return self  # Call from the class
        return obj.__dict__.get(self.name, self.default)

    def __set__(self, obj, value):
        """
        Called when assigning a value to a property.
        """
        if self.required and value is None:
            raise ValueError("Property %s is required" % self.name)
        if value is not None:
            value = self.validate(value)
        obj.__dict__[self.name] = value

    def check(self, obj):
        """
        Called after an object is initialized to detect unset required
        properties.
        """
        if self.required and obj.__dict__.get(self.name) is None:
            raise ValueError("Property %s is required" % self.name)

    def validate(self, value):
        """
        Should be implemented by subclass to validate value.

        Called when assigning a value to a property.
        """
        return value


class Enum(Property):

    def __init__(self, required=False, default=None, doc=None, values=()):
        if not required and default not in values:
            raise ValueError("Default value %s not in allowed values %s" %
                             (default, values))
        super(Enum, self).__init__(required=required, default=default, doc=doc)
        self.values = values

    def validate(self, value):
        if value not in self.values:
            raise ValueError("Invalid value %r for property %s" %
                             (value, self.name))
        return value


class String(Property):

    def validate(self, value):
        if not isinstance(value, six.string_types):
            raise ValueError("Invalid value %r for string property %s" %
                             (value, self.name))
        return value


class _Number(Property):

    def __init__(self, required=False, default=None, doc=None, minval=None,
                 maxval=None):
        if minval is not None and default is not None and default < minval:
            raise ValueError("Invalid default %s < %s" % (default, minval))
        if maxval is not None and default is not None and default > maxval:
            raise ValueError("Invalid default %s > %s" % (default, maxval))
        super(_Number, self).__init__(required=required, default=default,
                                      doc=doc)
        self.minval = minval
        self.maxval = maxval

    def validate(self, value):
        if self.minval is not None and value < self.minval:
            raise ValueError("Invalid value %s < %s for property %s" %
                             (value, self.minval, self.name))
        if self.maxval is not None and value > self.maxval:
            raise ValueError("Invalid value %s > %s for property %s" %
                             (value, self.maxval, self.name))
        return value


class Integer(_Number):

    def validate(self, value):
        if not isinstance(value, int):
            raise ValueError("Invalid value %r for integer property %s" %
                             (value, self.name))
        return super(Integer, self).validate(value)


class Float(_Number):

    def validate(self, value):
        if not isinstance(value, float):
            raise ValueError("Invalid value %r for float property %s" %
                             (value, self.name))
        return super(Float, self).validate(value)


class Boolean(Property):

    def validate(self, value):
        if value not in (True, False):
            raise ValueError("Invalid value %r for boolean property %s" %
                             (value, self.name))
        return value


class UUID(Property):

    def validate(self, value):
        return str(uuid.UUID(value))


class Password(Property):

    def __init__(self, required=False, default=None, doc=None, decode=None):
        super(Password, self).__init__(required=required, default=default,
                                       doc=doc)
        self.decode = decode

    def validate(self, password):
        if not isinstance(password, ProtectedPassword):
            raise ValueError("Not a ProtectedPassword %s" % type(password))
        if self.decode:
            password.value = self.decode(password.value)
        return password


def decode_base64(value):
    try:
        return base64.b64decode(value)
    except TypeError as e:
        raise ValueError("Unable to decode base64 value %s" % e)


class OwnerType(type):

    def __new__(cls, name, bases, dct):
        # Name properties used by this class
        for name, obj in dct.items():
            if isinstance(obj, Property):
                obj.name = name
        return type.__new__(cls, name, bases, dct)

    def __call__(self, *args, **kw):
        # Check properties after  object is initialized
        instance = super(OwnerType, self).__call__(*args, **kw)
        for name, obj in instance.__class__.__dict__.items():
            if isinstance(obj, Property):
                obj.check(instance)
        return instance


@six.add_metaclass(OwnerType)
class Owner(object):
    """
    Base class for classes using properties

    Inheriting from this class, all properties on this class and subclasses
    will be automatically named and required properties will raise ValueError
    if not initialized.
    """
