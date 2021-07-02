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
import pytest

from vdsm.storage import mpathconf

from testing import on_fedora
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

skip_on_fedora_30 = pytest.mark.skipif(
    on_fedora('30'), reason="Test hangs on fc30 CI")


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


@skip_on_fedora_30
@requires_selinux
def test_configure_blacklist(fake_conf):
    wwids = {"wwid1", "wwid2", "wwid3"}
    mpathconf.configure_blacklist(wwids)
    assert fake_conf.read() == mpathconf._HEADER + CONF


@skip_on_fedora_30
@requires_selinux
def test_read_blacklist(fake_conf):
    wwids = {"wwid1", "wwid2", "wwid3"}
    mpathconf.configure_blacklist(wwids)
    assert mpathconf.read_blacklist() == wwids


@skip_on_fedora_30
@requires_selinux
def test_read_empty_blacklist(fake_conf):
    mpathconf.configure_blacklist([])
    wwids = mpathconf.read_blacklist()
    assert not wwids


def test_read_no_blacklist(fake_conf):
    wwids = mpathconf.read_blacklist()
    assert not wwids


@skip_on_fedora_30
@requires_selinux
def test_read_bad_blacklist(fake_conf):
    mpathconf.configure_blacklist([])
    # Overwrite conf with a bad conf.
    fake_conf.write(BAD_CONF)
    wwids = mpathconf.read_blacklist()
    assert wwids == {"wwid1", "wwid2", "wwid5"}
