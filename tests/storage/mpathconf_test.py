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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
import io

import pytest

from vdsm.storage import mpathconf
from vdsm.storage.mpathconf import Section, Pair

from . marks import requires_selinux

CONF = """\
blacklist {
    wwid "wwid1"
    wwid "wwid2"
    wwid "wwid3"
}
"""

EMPTY_CONF = """\
blacklist {

}
"""

BAD_CONF = """\
blacklist {
    wwid "wwid1" invalid
    wwid "wwid2" "wwid3"

    invalid
    invalid wwid "wwid4"
    wwid "wwid5"
}
"""


@pytest.fixture
def fake_conf(tmpdir, monkeypatch):
    fake_conf = tmpdir.join("multipath/conf.d/vdsm_blacklist.conf")
    monkeypatch.setattr(
        mpathconf, "_VDSM_MULTIPATH_BLACKLIST", str(fake_conf))
    return fake_conf


def test_format_blacklist():
    wwids = {"wwid1", "wwid2", "wwid3"}
    conf = mpathconf.format_blacklist(wwids)
    assert conf == CONF


def test_format_empty_blacklist():
    conf = mpathconf.format_blacklist([])
    assert conf == EMPTY_CONF


@requires_selinux
def test_configure_blacklist(fake_conf):
    wwids = {"wwid1", "wwid2", "wwid3"}
    mpathconf.configure_blacklist(wwids)
    assert fake_conf.read() == mpathconf._HEADER + CONF


@requires_selinux
def test_read_blacklist(fake_conf):
    wwids = {"wwid1", "wwid2", "wwid3"}
    mpathconf.configure_blacklist(wwids)
    assert mpathconf.read_blacklist() == wwids


@requires_selinux
def test_read_empty_blacklist(fake_conf):
    mpathconf.configure_blacklist([])
    wwids = mpathconf.read_blacklist()
    assert not wwids


def test_read_no_blacklist(fake_conf):
    wwids = mpathconf.read_blacklist()
    assert not wwids


@requires_selinux
def test_read_bad_blacklist(fake_conf):
    mpathconf.configure_blacklist([])
    # Overwrite conf with a bad conf.
    fake_conf.write(BAD_CONF)
    wwids = mpathconf.read_blacklist()
    assert wwids == {"wwid1", "wwid2", "wwid5"}


@pytest.fixture
def fake_mpath_conf(tmpdir, monkeypatch):
    fake_conf = tmpdir.join("multipath.conf")
    monkeypatch.setattr(mpathconf, "CONF_FILE", str(fake_conf))
    return fake_conf


def test_mpath_conf_create(fake_mpath_conf):
    # Generated multipath configuration shall contain a valid revision number.
    mpathconf.configure_multipathd()
    assert mpathconf.read_metadata() == mpathconf.Metadata(
        revision=mpathconf.REVISION_OK,
        private=False,
    )


def test_mpath_current_tag_conf(fake_mpath_conf):
    data = f"""\
{mpathconf.CURRENT_TAG}
"""
    fake_mpath_conf.write(data)
    assert mpathconf.read_metadata() == mpathconf.Metadata(
        revision=mpathconf.REVISION_OK,
        private=False,
    )


def test_mpath_private_current_tag_conf(fake_mpath_conf):
    data = f"""\
{mpathconf.CURRENT_TAG}
# VDSM PRIVATE
"""
    fake_mpath_conf.write(data)
    assert mpathconf.read_metadata() == mpathconf.Metadata(
        revision=mpathconf.REVISION_OK,
        private=True,
    )


def test_mpath_private_old_tag_conf(fake_mpath_conf):
    data = """\
# VDSM REVISION 1.5
# VDSM PRIVATE
"""
    fake_mpath_conf.write(data)
    assert mpathconf.read_metadata() == mpathconf.Metadata(
        revision=mpathconf.REVISION_OLD,
        private=True,
    )


def test_mpath_old_tag_conf(fake_mpath_conf):
    data = """\
# RHEV REVISION 1.0
"""
    fake_mpath_conf.write(data)
    assert mpathconf.read_metadata() == mpathconf.Metadata(
        revision=mpathconf.REVISION_OLD,
        private=False,
    )


def test_mpath_empty_conf(fake_mpath_conf):
    fake_mpath_conf.write("")
    assert mpathconf.read_metadata() == mpathconf.Metadata(
        revision=mpathconf.REVISION_MISSING,
        private=False,
    )


def test_mpathd_parser():
    mpath_out = """\
section1 {
    key1 "value 1"
    key2 value2
    key3 3
}
section2 {
    key1 value1
}
section3 {
}
section4 {
    entry {
        key1 "value 1"
        key2 "value 2"
        key3 "value 3"
    }
    entry {
        key1 "value1"
    }
    entry {
    }
}
section5 {
    key1 value1
    key2 value2
    entry {
        key1 value1
        key2 value2
    }
    entry {
        key1 value1
    }
}"""
    assert mpathconf._parse_conf(io.StringIO(mpath_out)) == [
        Section('section1', [
            Pair('key1', 'value 1'),
            Pair('key2', 'value2'),
            Pair('key3', '3')
        ]),
        Section('section2', [
            Pair('key1', 'value1')
        ]),
        Section('section3', [
        ]),
        Section('section4', [
            Section('entry', [
                Pair('key1', 'value 1'),
                Pair('key2', 'value 2'),
                Pair('key3', 'value 3')
            ]),
            Section('entry', [
                Pair('key1', 'value1')
            ]),
            Section('entry', [])
        ]),
        Section('section5', [
            Pair('key1', 'value1'),
            Pair('key2', 'value2'),
            Section('entry', [
                Pair('key1', 'value1'),
                Pair('key2', 'value2')
            ]),
            Section('entry', [
                Pair('key1', 'value1')
            ])
        ]),
    ]


def test_mpathd_ufn_enabled_single():
    fake_conf = [
        Section('defaults', [
            Pair('key', 'value'),
            Pair('user_friendly_names', 'yes'),
        ]),
        Section('devices', [
            Section('device', [
                Pair('user_friendly_names', 'no'),
                Pair('key', 'value'),
            ]),
            Section('device', [
                Pair('key', 'value')
            ])
        ]),
    ]
    issues = mpathconf._check_conf(fake_conf)
    assert issues == [
        Section('defaults', [
            Pair('key', 'value'),
            Pair('user_friendly_names', 'yes'),
        ])
    ]


def test_mpathd_ufn_enabled_multiple():
    fake_conf = [
        Section('defaults', [
            Pair('key', 'value'),
            Pair('user_friendly_names', 'yes'),
        ]),
        Section('overrides', [
            Pair('user_friendly_names', 'yes'),
        ]),
        Section('devices', [
            Section('device', [
                Pair('user_friendly_names', 'yes'),
                Pair('key', 'value'),
            ]),
            Section('device', [
                Pair('key1', 'value1'),
                Pair('user_friendly_names', 'yes'),
                Pair('key2', 'value2'),
            ]),
            Section('device', [
                Pair('key', 'value')
            ])
        ]),
    ]
    issues = mpathconf._check_conf(fake_conf)
    assert issues == [
        Section('defaults', [
            Pair('key', 'value'),
            Pair('user_friendly_names', 'yes'),
        ]),
        Section('overrides', [
            Pair('user_friendly_names', 'yes'),
        ]),
        Section('device', [
            Pair('user_friendly_names', 'yes'),
            Pair('key', 'value'),
        ]),
        Section('device', [
            Pair('key1', 'value1'),
            Pair('user_friendly_names', 'yes'),
            Pair('key2', 'value2'),
        ]),
    ]


def test_mpathd_ufn_blacklist():
    fake_conf = [
        Section('defaults', [
            Pair('key', 'value'),
            Pair('user_friendly_names', 'no'),
        ]),
        Section('blacklist', [
            Pair('key', 'value'),
            Section('name1', [
                Pair('key1', 'value1'),
                Pair('key2', 'value2'),
            ]),
            Section('name2', [
                Pair('key', 'value')
            ])
        ]),
        Section('blacklist_exceptions', [
            Pair('key', 'value'),
            Section('name3', [
                Pair('key1', 'value1'),
                Pair('key2', 'value2'),
            ]),
            Section('name4', [
                Pair('key', 'value')
            ])
        ]),
    ]
    issues = mpathconf._check_conf(fake_conf)
    assert not issues


def test_mpathd_ufn_no_pairs():
    fake_conf = [
        Section('section1', [])
    ]
    issues = mpathconf._check_conf(fake_conf)
    assert not issues


def test_mpathd_ufn_empty():
    fake_conf = []
    issues = mpathconf._check_conf(fake_conf)
    assert not issues


def test_mpathd_ufn_all_disabled():
    fake_conf = [
        Section('defaults', [
            Pair('key', 'value'),
            Pair('user_friendly_names', 'no'),
        ]),
        Section('overrides', [
            Pair('key1', 'value1'),
            Pair('user_friendly_names', 'no'),
            Pair('key2', 'value2'),
        ]),
        Section('devices', [
            Section('device', [
                Pair('user_friendly_names', 'no'),
                Pair('key', 'value'),
            ]),
            Section('device', [
                Pair('key', 'value')
            ])
        ]),
    ]
    issues = mpathconf._check_conf(fake_conf)
    assert not issues
