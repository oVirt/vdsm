#
# Copyright 2012-2016 Red Hat, Inc.
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

import six

from collections import defaultdict

import pytest

from vdsm.common.compat import get_args_spec
from vdsm.common.exception import GeneralException
from vdsm.common.exception import VdsmException
from vdsm.storage import exception as storage_exception


def find_module_exceptions(module, base_class=None):
    for name in dir(module):
        obj = getattr(module, name)

        if not isinstance(obj, type):
            continue

        if base_class and not issubclass(obj, base_class):
            continue

        yield obj


def test_collisions():
    codes = defaultdict(list)

    for obj in find_module_exceptions(storage_exception, GeneralException):
        codes[obj.code].append(obj.__name__)

    problems = [(k, v) for k, v in six.iteritems(codes)
                if len(v) != 1 or k >= 5000]

    assert not problems, "Duplicated or invalid exception code"


def test_info():
    skipped_exceptions = [
        storage_exception.VolumeGroupDoesNotExist,
        storage_exception.InaccessiblePhysDev,
    ]
    for obj in find_module_exceptions(storage_exception, VdsmException):
        if obj in skipped_exceptions:
            # Skip since a constructor parameter requires a specific type.
            # This exception should be tested separately.
            continue

        # Inspect exception object initialization parameters.
        args, varargs, kwargs = get_args_spec(obj.__init__)

        # Prepare fake parameters for object initialization.
        # We ignore the 'self' argument from counting.
        args = [FakeArg(i) for i in range(len(args) - 1)]
        if varargs:
            args.append(FakeArg(len(args)))
            args.append(FakeArg(len(args)))
        kwargs = {kwargs: FakeArg(len(args))} if kwargs else {}

        # Instantiate the traversed exception object.
        e = obj(*args, **kwargs)
        assert e.info() == {
            "code": e.code,
            "message": str(e)
        }


def test_VolumeGroupDoesNotExist():
    # Require a VG name or UUID at initialization.
    # Empty constructor shall raise.
    with pytest.raises(ValueError):
        e = storage_exception.VolumeGroupDoesNotExist()

    # Expected error type is LVMCommandError.
    with pytest.raises(TypeError):
        e = storage_exception.VolumeGroupDoesNotExist("vg-name", error="error")

    # Correct initialization.
    fake_error = storage_exception.LVMCommandError(
        rc=5, cmd=["fake"], out=["fake output"], err=["fake error"])
    e = storage_exception.VolumeGroupDoesNotExist("vg-name", error=fake_error)
    assert e.error == fake_error
    # Check error format
    formatted = str(e)
    assert "vg_name=vg-name" in formatted
    assert f'error={fake_error}'.replace('\'', r'\'') in formatted
    assert "vg_uuid=" not in formatted


def test_InaccessiblePhysDev():
    # Expected error type is LVMCommandError.
    with pytest.raises(TypeError):
        e = storage_exception.InaccessiblePhysDev("pv-name", error="error")

    # Correct initialization.
    fake_error = storage_exception.LVMCommandError(
        rc=5, cmd=["fake"], out=["fake output"], err=["fake error"])
    e = storage_exception.InaccessiblePhysDev("pv-name", error=fake_error)
    assert e.error == fake_error
    # Check error format
    formatted = str(e)
    assert "devices=pv-name" in formatted
    assert f'error={fake_error}'.replace('\'', r'\'') in formatted


class FakeArg(int):
    def __getitem__(self, name):
        return self
