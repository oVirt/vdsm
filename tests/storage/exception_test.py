# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import inspect

import six

from collections import defaultdict

import pytest

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

        # Internal classes are skipped.
        if name.startswith('_'):
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
    for obj in find_module_exceptions(storage_exception, VdsmException):
        if issubclass(obj, storage_exception._HoldingLVMCommandError):
            # Skip since a constructor parameter requires a specific type.
            # This exception should be tested separately.
            continue

        # Inspect exception object initialization parameters.
        spec = inspect.getfullargspec(obj.__init__)

        # Prepare fake parameters for object initialization.
        # We ignore the 'self' argument from counting.
        args = [FakeArg(i) for i in range(len(spec.args) - 1)]
        if spec.varargs:
            args.append(FakeArg(len(args)))
            args.append(FakeArg(len(args)))
        kwargs = {spec.varkw: FakeArg(len(args))} if spec.varkw else {}

        # Instantiate the traversed exception object.
        e = obj(*args, **kwargs)
        assert e.info() == {
            "code": e.code,
            "message": str(e)
        }


def test_LogicalVolumeDoesNotExistError():
    # Expected error type is LVMCommandError.
    with pytest.raises(TypeError):
        e = storage_exception.LogicalVolumeDoesNotExistError(
            "vg-name", "lv-name", error="error")

    # Correct initialization.
    fake_error = storage_exception.LVMCommandError(
        rc=5, cmd=["fake"], out=["fake output"], err=["fake error"])
    e = storage_exception.LogicalVolumeDoesNotExistError(
        "vg-name", "lv-name", error=fake_error)
    assert e.error == fake_error
    # Check error format
    formatted = str(e)
    assert "vg_name=vg-name" in formatted
    assert "lv_name=lv-name" in formatted
    assert "error=" in formatted


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
    assert "error=" in formatted
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
    assert "error=" in formatted


class FakeArg(int):
    def __getitem__(self, name):
        return self
