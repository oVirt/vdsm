#
# Copyright 2020 Red Hat, Inc.
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

import pytest

from vdsm.storage import sanlockconf

EXAMPLE = """
# Example sanlock configuration file.
# Comments and empty lines are ignored

# Options can use key=value:
key1=1

# Or key = value:
key2 = 2

# values can contain =:
key3 = =3=

# values can contain whitespace:
key4 = 4 4

# Lines with leading whitespace are ignored:
 key5 = 5
\tkey6 = 6
 # comment key7 = 7

# Lines without = are ignored:
key8 8

# Empty keys are ignored:
= 9

# That's all!
"""


@pytest.fixture
def tmpconf(tmpdir, monkeypatch):
    path = str(tmpdir.join("sanlock.conf"))
    monkeypatch.setattr(sanlockconf, "SANLOCK_CONF", path)
    return path


def test_no_sanlock_conf(tmpconf):
    assert sanlockconf.load() == {}


def test_empty_sanlock_conf(tmpconf):
    with open(tmpconf, "w") as f:
        f.write("")
    assert sanlockconf.load() == {}


def test_load(tmpconf):
    with open(tmpconf, "w") as f:
        f.write(EXAMPLE)

    assert sanlockconf.load() == {
        "key1": "1",
        "key2": "2",
        "key3": "=3=",
        "key4": "4 4",
    }


def test_dump_create(tmpconf):
    conf = {"key1": "1", "key2": "2"}
    backup = sanlockconf.dump(conf)

    assert backup is None
    assert sanlockconf.load() == conf


def test_dump_replace(tmpconf):
    sanlockconf.dump({"key1": "1", "key2": "2"})
    with open(sanlockconf.SANLOCK_CONF) as f:
        old_text = f.read()

    conf = {"key1": "new1", "key2": "2", "key3": "new2"}
    backup = sanlockconf.dump(conf)

    assert backup.startswith(sanlockconf.SANLOCK_CONF + ".")
    with open(backup) as f:
        assert f.read() == old_text
    assert sanlockconf.load() == conf
