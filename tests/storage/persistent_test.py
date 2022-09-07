# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.storage import exception as se
from vdsm.storage import persistent


class ReadError(Exception):
    """ Raised while reading from storage """


class WriteError(Exception):
    """ Raised while writing to storage """


class UserError(Exception):
    """ Raised by user code inside a transaction """


class MemoryBackend(object):

    def __init__(self, lines=(), fail_read=False, fail_write=False):
        self.lines = list(lines)
        self.fail_read = fail_read
        self.fail_write = fail_write
        self.version = 0

    def readlines(self):
        if self.fail_read:
            raise ReadError
        return self.lines[:]

    def writelines(self, lines):
        if self.fail_write:
            raise WriteError
        self.lines = lines[:]
        self.version += 1


class TestDictValidator:

    VALID_FIELDS = {
        "int_str": (int, str),
        "str_str": (str, str),
        "func_func": (lambda s: s.lower(), lambda s: s.upper()),
    }

    def test_length(self):
        pd = persistent.PersistentDict(MemoryBackend())
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)

        assert len(dv) == 0

        dv["str_str"] = "value 1"
        assert len(pd) == 1
        assert len(dv) == 1

        dv["func_func"] = "value 2"
        assert len(pd) == 2
        assert len(dv) == 2

    def test_set(self):
        pd = persistent.PersistentDict(MemoryBackend())
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)
        dv["str_str"] = "value 1"

        # test item was created and underlying dict has same value
        assert pd["str_str"] == "value 1"
        assert dv["str_str"] == "value 1"

    def test_read(self):
        pd = persistent.PersistentDict(MemoryBackend())
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)
        dv["str_str"] = "value 1"

        # test item read
        assert dv.get("str_str") == "value 1"
        assert dv["str_str"] == "value 1"

        # test read item which doesn't exists
        assert dv.get("int_str") is None

        # test read item which is not allowed
        with pytest.raises(KeyError):
            dv.get("not-exists")

    def test_delete(self):
        pd = persistent.PersistentDict(MemoryBackend())
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)
        dv["str_str"] = "value 1"
        dv["func_func"] = "value 2"

        del dv["func_func"]

        # test second item was removed
        assert "func_func" not in pd
        assert "func_func" not in dv

        # and first one is still there
        assert "str_str" in pd
        assert "str_str" in dv

    def test_contains(self):
        pd = persistent.PersistentDict(MemoryBackend())
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)
        dv["str_str"] = "value 1"

        # test contains operation
        assert "str_str" in dv
        assert "int_str" not in dv
        assert "not-exists" not in dv

    def test_key_iteration(self):
        pd = persistent.PersistentDict(MemoryBackend())
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)
        dv["int_str"] = 1
        dv["str_str"] = "2"
        dv["func_func"] = "3"
        expected = {"int_str", "str_str", "func_func"}

        assert set(dv.iterkeys()) == expected
        assert set(dv) == expected
        assert set(dv.keys()) == expected

    def test_update(self):
        pd = persistent.PersistentDict(MemoryBackend())
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)
        dv["int_str"] = 1
        dv["str_str"] = "2"
        dv["func_func"] = "old value"

        updates = {"int_str": 4, "str_str": "5", "func_func": "new value"}
        dv.update(updates)

        assert pd["int_str"] == "4"
        assert dv["int_str"] == 4
        assert pd["str_str"] == "5"
        assert dv["str_str"] == "5"
        assert pd["func_func"] == "NEW VALUE"
        assert dv["func_func"] == "new value"

    def test_clear(self):
        pd = persistent.PersistentDict(MemoryBackend())
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)
        dv["int_str"] = 1
        dv["str_str"] = "2"

        dv.clear()

        assert not pd
        assert not dv

    def test_copy(self):
        pd = persistent.PersistentDict(MemoryBackend())
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)
        dv_expected = {"int_str": 1, "str_str": "2", "func_func": "3"}
        dv.update(dv_expected)

        dv_copy = dv.copy()

        assert dv_copy == dv_expected

        # update the copy
        dv_copy["int_str"] = 4
        dv_copy["str_str"] = "5"
        dv_copy["func_func"] = "6"

        # and check the original dict is intact
        assert dv.copy() == dv_expected

    def test_copy_with_invalid_items(self):
        pd = persistent.PersistentDict(MemoryBackend())
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)
        dv_expected = {"int_str": 1, "str_str": "2", "func_func": "3"}
        dv.update(dv_expected)

        dv._dict["invalid_key"] = "invalid value"
        dv_expected.update({"invalid_key": "invalid value"})

        dv_copy = dv.copy()

        # Expected behaviour is that even invalid items are still kept in the
        # dict. This is used e.g. in spbackends.py
        assert dv_copy == dv_expected

    def test_encode(self):
        pd = persistent.PersistentDict(MemoryBackend())
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)
        dv["func_func"] = "value"
        assert pd["func_func"] == "VALUE"

    def test_decode(self):
        pd = persistent.PersistentDict(MemoryBackend())
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)
        pd["func_func"] = "VALUE"
        assert dv["func_func"] == "value"

    def test_persistent_transaction(self):
        b = MemoryBackend()
        pd = persistent.PersistentDict(b)
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)

        # Transaction flushes lines to storage once.
        with dv.transaction():
            dv["str_str"] = "value 1"
            dv["func_func"] = "value 2"

        assert dv["str_str"] == "value 1"
        assert dv["func_func"] == "value 2"
        assert b.version == 1

    def test_invalidate(self):
        b = MemoryBackend([
            "int_str=1",
            "str_str=2",
            "_SHA_CKSUM=fd58b7962408a4956bd694d617a1201306b363c2",
        ])
        pd = persistent.PersistentDict(b)
        dv = persistent.DictValidator(pd, self.VALID_FIELDS)

        # Trigger reading from storage.
        assert dv["int_str"] == 1

        # Storage contents changed from another host...
        b.lines = [
            "int_str=1",
            "str_str=2",
            "func_func=3",
            "_SHA_CKSUM=5e5ad85614c502d9a2f44d0473b9384ac49eedff",
        ]

        # Return value read before.
        assert dv["str_str"] == "2"
        assert "func_func" not in dv

        # Invalidating the dict will cause the next get to read again from
        # storage.
        dv.invalidate()

        assert dv["int_str"] == 1
        assert dv["str_str"] == "2"
        assert dv["func_func"] == "3"


class TestPersistentDict:

    def test_len(self):
        b = MemoryBackend()
        pd = persistent.PersistentDict(b)

        assert len(pd) == 0

        pd["key 1"] = "value 1"
        assert len(pd) == 1

        pd["key 2"] = "value 2"
        assert len(pd) == 2

    def test_contains(self):
        b = MemoryBackend()
        pd = persistent.PersistentDict(b)

        assert "key" not in pd
        pd["key"] = "value"
        assert "key" in pd

    def test_get_good_checksum(self):
        b = MemoryBackend([
            "key 1=value 1",
            "key 2=value 2",
            "_SHA_CKSUM=ad4e8ffdd89dde809bf1ed700838b590b08a3826",
        ])
        pd = persistent.PersistentDict(b)

        assert pd["key 1"] == "value 1"
        assert pd["key 2"] == "value 2"

    def test_get_no_checksum(self):
        initial_lines = [
            "key 1=value 1",
            "key 2=value 2",
        ]
        b = MemoryBackend(initial_lines)
        pd = persistent.PersistentDict(b)

        assert pd["key 1"] == "value 1"
        assert pd["key 2"] == "value 2"

        # Storage not modified by reading.
        assert b.lines == initial_lines

    def test_get_bad_checksum(self):
        initial_lines = [
            "key 1=value 1",
            "key 2=value 2",
            "_SHA_CKSUM=badchecksum",
        ]
        b = MemoryBackend(initial_lines)
        pd = persistent.PersistentDict(b)

        with pytest.raises(se.MetaDataSealIsBroken):
            pd["key 1"]

        # Storage not modified by reading.
        assert b.lines == initial_lines

    def test_getitem_setitem(self):
        b = MemoryBackend()
        pd = persistent.PersistentDict(b)

        with pytest.raises(KeyError):
            pd["key"]

        pd["key 1"] = "value 1"
        assert pd["key 1"] == "value 1"

        pd["key 2"] = "value 2"
        assert pd["key 1"] == "value 1"
        assert pd["key 2"] == "value 2"

        pd.update({"key 3": "value 3", "key 2": "new value 2"})
        assert pd["key 1"] == "value 1"
        assert pd["key 2"] == "new value 2"
        assert pd["key 3"] == "value 3"

    def test_get(self):
        b = MemoryBackend()
        pd = persistent.PersistentDict(b)

        assert pd.get("key") is None
        pd["key"] = "value"
        assert pd.get("key") == "value"

    def test_del(self):
        b = MemoryBackend(["key=value"])
        pd = persistent.PersistentDict(b)

        del pd["key"]
        assert "key" not in pd

    def test_del_missing(self):
        b = MemoryBackend()
        pd = persistent.PersistentDict(b)

        with pytest.raises(KeyError):
            del pd["key"]

    def test_iter(self):
        b = MemoryBackend(["key 1=1", "key 2=2"])
        pd = persistent.PersistentDict(b)

        assert set(pd) == {"key 1", "key 2"}

    def test_clear(self):
        b = MemoryBackend([
            "key 1=value 1",
            "key 2=value 2",
            "_SHA_CKSUM=ad4e8ffdd89dde809bf1ed700838b590b08a3826",
        ])
        pd = persistent.PersistentDict(b)

        # Trigger reading from storage.
        pd["key 1"]

        # Clears all keys.
        pd.clear()
        assert "key 1" not in pd
        assert "key 2" not in pd

        # Also flush change to storage (includes checksum).
        assert b.lines == [
            "_SHA_CKSUM=da39a3ee5e6b4b0d3255bfef95601890afd80709"
        ]

    def test_storage(self):
        b = MemoryBackend()
        pd = persistent.PersistentDict(b)

        # Setting value flush dict to writer.
        pd["key 1"] = "value 1"
        assert b.lines == [
            "key 1=value 1",
            "_SHA_CKSUM=fce57dc690209dc4109d993de9c11d72c8ffd4b6",
        ]
        assert b.version == 1

        # Setting another value flush entire dict again.
        pd["key 2"] = "value 2"
        assert b.lines == [
            "key 1=value 1",
            "key 2=value 2",
            "_SHA_CKSUM=ad4e8ffdd89dde809bf1ed700838b590b08a3826",
        ]
        assert b.version == 2

        # Updating flush entire dict again.
        pd.update({"key 3": "value 3", "key 2": "new value 2"})
        assert b.lines == [
            "key 1=value 1",
            "key 2=new value 2",
            "key 3=value 3",
            "_SHA_CKSUM=96cff78771397697ce609321364aabc818299be8",
        ]
        assert b.version == 3

    def test_transaction(self):
        b = MemoryBackend()
        pd = persistent.PersistentDict(b)

        # Transaction flushes lines to storage once.
        with pd.transaction():
            pd["key 1"] = "value 1"
            pd["key 2"] = "value 2"

        assert pd["key 1"] == "value 1"
        assert pd["key 2"] == "value 2"
        assert b.version == 1

    def test_transaction_nested(self):
        b = MemoryBackend()
        pd = persistent.PersistentDict(b)
        # Transaction flushes lines to storage once.
        with pd.transaction():
            pd["key 1"] = "value 1"
            with pd.transaction():
                pd["key 2"] = "value 2"

        assert pd["key 1"] == "value 1"
        assert pd["key 2"] == "value 2"
        assert b.version == 1

    def test_invalidate(self):
        b = MemoryBackend([
            "key 1=value 1",
            "key 2=value 2",
            "_SHA_CKSUM=ad4e8ffdd89dde809bf1ed700838b590b08a3826",
        ])
        pd = persistent.PersistentDict(b)

        # Trigger reading from storage.
        assert pd["key 1"] == "value 1"

        # Storage contents changed from another host...
        b.lines = [
            "key 1=value 1",
            "key 2=new value 2",
            "key 3=value 3",
            "_SHA_CKSUM=96cff78771397697ce609321364aabc818299be8",
        ]

        # Return value read before.
        assert pd["key 2"] == "value 2"
        assert "key 3" not in pd

        # Invalidating the dict will cause the next get to read again from
        # storage.
        pd.invalidate()

        assert pd["key 1"] == "value 1"
        assert pd["key 2"] == "new value 2"
        assert pd["key 3"] == "value 3"

    def test_read_error(self):
        initial_lines = [
            "key 1=value 1",
            "key 2=value 2",
            "_SHA_CKSUM=ad4e8ffdd89dde809bf1ed700838b590b08a3826",
        ]
        b = MemoryBackend(lines=initial_lines, fail_read=True)
        pd = persistent.PersistentDict(b)

        # Trying to modify persistent dict should start a new tranaction and
        # fail the transaction while reading from storage.

        with pytest.raises(ReadError):
            pd["key 1"] = "new value 1"

        with pytest.raises(ReadError):
            del pd["key 1"]

        assert b.lines == initial_lines
        assert b.version == 0

    def test_write_error(self):
        initial_lines = [
            "key 1=value 1",
            "key 2=value 2",
            "_SHA_CKSUM=ad4e8ffdd89dde809bf1ed700838b590b08a3826",
        ]
        b = MemoryBackend(lines=initial_lines, fail_write=True)
        pd = persistent.PersistentDict(b)

        # All access to persistent dict should fail the transaction when trying
        # to modify storage, and rollback to previous state.

        with pytest.raises(WriteError):
            pd["key 1"] = "new value 1"

        assert pd["key 1"] == "value 1"
        assert pd["key 2"] == "value 2"

        with pytest.raises(WriteError):
            del pd["key 1"]

        assert pd["key 1"] == "value 1"
        assert pd["key 2"] == "value 2"

        assert b.lines == initial_lines
        assert b.version == 0

    def test_transaction_user_error(self):
        b = MemoryBackend()
        pd = persistent.PersistentDict(b)

        # User error during the transaction should abort the entire
        # transaction, otherwise we may leave partial changes on storage.
        with pytest.raises(UserError):
            with pd.transaction():
                pd["key 1"] = 1
                raise UserError

        # Nothing should be written since the transaction was aborted.
        assert b.lines == []

    def test_nested_transaction_user_error(self):
        b = MemoryBackend()
        pd = persistent.PersistentDict(b)

        # User error during the transaction should abort the entire
        # transaction, otherwise we may leave partial changes on storage.
        with pytest.raises(UserError):
            with pd.transaction():
                pd["key 1"] = 1
                with pd.transaction():
                    pd["key 2"] = 2
                    raise UserError

        # Nothing should be written since the transaction was aborted.
        assert b.lines == []

    def test_transient_read_error(self):
        initial_lines = [
            "key 1=value 1",
            "key 2=value 2",
            "_SHA_CKSUM=ad4e8ffdd89dde809bf1ed700838b590b08a3826",
        ]
        b = MemoryBackend(lines=initial_lines)
        pd = persistent.PersistentDict(b)

        # Simulate transient error on storage.
        b.fail_read = True

        with pytest.raises(ReadError):
            pd["key 2"] = "new value 2"

        # Nothing should be written since the transaction was aborted.
        assert b.lines == initial_lines
        assert b.version == 0

        # Restore storage, reading and writing should work now.
        b.fail_read = False

        pd["key 2"] = "new value 2"

        # Both dict and storage should change.
        assert pd["key 1"] == "value 1"
        assert pd["key 2"] == "new value 2"
        assert b.lines == [
            "key 1=value 1",
            "key 2=new value 2",
            "_SHA_CKSUM=3c313d2c72ab17086f75350f5cf71d9a42655419",
        ]
        assert b.version == 1

    def test_transient_write_error(self):
        initial_lines = [
            "key 1=value 1",
            "key 2=value 2",
            "_SHA_CKSUM=ad4e8ffdd89dde809bf1ed700838b590b08a3826",
        ]
        b = MemoryBackend(lines=initial_lines)
        pd = persistent.PersistentDict(b)

        # Simulate transient error on storage.
        b.fail_write = True

        with pytest.raises(WriteError):
            pd["key 2"] = "new value 2"

        # Nothing should change.
        assert pd["key 1"] == "value 1"
        assert pd["key 2"] == "value 2"
        assert b.lines == initial_lines
        assert b.version == 0

        # Restore storage, writing should work now.
        b.fail_write = False

        pd["key 2"] = "new value 2"

        # Both dict and storage should change.
        assert pd["key 1"] == "value 1"
        assert pd["key 2"] == "new value 2"
        assert b.lines == [
            "key 1=value 1",
            "key 2=new value 2",
            "_SHA_CKSUM=3c313d2c72ab17086f75350f5cf71d9a42655419",
        ]
        assert b.version == 1
