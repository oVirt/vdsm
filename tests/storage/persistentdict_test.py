#
# Copyright 2012-2019 Red Hat, Inc.
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

import pytest
from vdsm.storage import persistent


class WriterError(Exception):
    """ Raised while writing or reading """


class UserError(Exception):
    """ Raised by user code inside a transaction """


class MemoryWriter(object):

    def __init__(self, fail=False):
        self.lines = []
        self.fail = fail

    def readlines(self):
        if self.fail:
            raise WriterError
        return self.lines[:]

    def writelines(self, lines):
        if self.fail:
            raise WriterError
        self.lines = lines[:]


def test_persistent_dict_write_fail():
    pd = persistent.PersistentDict(MemoryWriter(fail=True))
    with pytest.raises(WriterError):
        pd["key"] = 1


def test_persistent_dict_nested_transaction_fail():
    pd = persistent.PersistentDict(MemoryWriter(fail=True))
    # TODO: This looks like a bug - we should raise the user error during the
    # transaction.
    with pytest.raises(WriterError):
        with pd.transaction():
            with pd.transaction():
                raise UserError
