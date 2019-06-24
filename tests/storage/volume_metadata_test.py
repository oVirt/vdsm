# Copyright 2016-2017 Red Hat, Inc.
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
from __future__ import print_function

import six
import textwrap
import time

import pytest

from testlib import make_uuid

from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import volume

from . constants import CLEARED_VOLUME_METADATA

MB = 1024 ** 2
FAKE_TIME = 1461095629


def make_init_params(**kwargs):
    res = dict(
        domain=make_uuid(),
        image=make_uuid(),
        puuid=make_uuid(),
        capacity=1024 * MB,
        format=sc.type2name(sc.RAW_FORMAT),
        type=sc.type2name(sc.SPARSE_VOL),
        voltype=sc.type2name(sc.LEAF_VOL),
        disktype=sc.DATA_DISKTYPE,
        description="",
        legality=sc.LEGAL_VOL,
        generation=sc.DEFAULT_GENERATION)
    res.update(kwargs)
    return res


def make_md_dict(**kwargs):
    res = {
        sc.DOMAIN: 'domain',
        sc.IMAGE: 'image',
        sc.PUUID: 'parent',
        sc.CAPACITY: '0',
        sc.FORMAT: 'format',
        sc.TYPE: 'type',
        sc.VOLTYPE: 'voltype',
        sc.DISKTYPE: 'disktype',
        sc.DESCRIPTION: 'description',
        sc.LEGALITY: 'legality',
        sc.CTIME: '0',
        sc.GENERATION: '1',
    }
    res.update(kwargs)
    return res


def make_lines(**kwargs):
    data = make_md_dict(**kwargs)
    # Emulate "key=value" lines read from VolumeMD storage as bytes
    lines = [b'EOF']
    for k, v in data.items():
        if v is not None:
            line = ("%s=%s" % (k, v)).encode("utf-8")
            lines.insert(0, line)
    return lines


class TestVolumeMetadata:

    def test_create_info(self, monkeypatch):
        params = make_init_params()
        expected = dict(
            CTIME=str(FAKE_TIME),
            DESCRIPTION=params['description'],
            DISKTYPE=params['disktype'],
            DOMAIN=params['domain'],
            FORMAT=params['format'],
            IMAGE=params['image'],
            LEGALITY=params['legality'],
            PUUID=params['puuid'],
            CAP=str(params['capacity']),
            TYPE=params['type'],
            VOLTYPE=params['voltype'],
            GEN=params['generation'])

        monkeypatch.setattr(time, 'time', lambda: FAKE_TIME)
        info = volume.VolumeMetadata(**params)
        for key, value in six.iteritems(expected):
            assert info[key] == value

    def test_storage_format_v4(self):
        params = make_init_params(ctime=FAKE_TIME)
        expected_params = dict(params)
        expected_params['size'] = params['capacity'] // sc.BLOCK_SIZE_512
        expected = textwrap.dedent("""\
            CTIME=%(ctime)s
            DESCRIPTION=%(description)s
            DISKTYPE=%(disktype)s
            DOMAIN=%(domain)s
            FORMAT=%(format)s
            GEN=%(generation)s
            IMAGE=%(image)s
            LEGALITY=%(legality)s
            MTIME=0
            PUUID=%(puuid)s
            SIZE=%(size)s
            TYPE=%(type)s
            VOLTYPE=%(voltype)s
            EOF
            """ % expected_params).encode("utf-8")
        md = volume.VolumeMetadata(**params)
        assert expected == md.storage_format(4)

    def test_storage_format_v5(self):
        params = make_init_params(ctime=FAKE_TIME)
        expected = textwrap.dedent("""\
            CAP=%(capacity)s
            CTIME=%(ctime)s
            DESCRIPTION=%(description)s
            DISKTYPE=%(disktype)s
            DOMAIN=%(domain)s
            FORMAT=%(format)s
            GEN=%(generation)s
            IMAGE=%(image)s
            LEGALITY=%(legality)s
            PUUID=%(puuid)s
            TYPE=%(type)s
            VOLTYPE=%(voltype)s
            EOF
            """ % params).encode("utf-8")
        md = volume.VolumeMetadata(**params)
        assert expected == md.storage_format(5)

    def test_storage_format_overrides(self):
        params = make_init_params()
        md = volume.VolumeMetadata(**params)
        data = md.storage_format(4, CAP=md.capacity).decode("utf-8")
        assert "SIZE=%s\n" % str(int(md.capacity) // sc.BLOCK_SIZE_512) in data
        assert "CAP=%s\n" % md.capacity in data

    @pytest.mark.parametrize("param", ['capacity', 'ctime'])
    def test_int_params_str_raises(self, param):
        params = make_init_params(**{param: 'not_an_int'})
        with pytest.raises(AssertionError):
            volume.VolumeMetadata(**params)

    @pytest.mark.parametrize("required_key",
                             [key for key in make_md_dict()
                              if key != sc.GENERATION])
    def test_from_lines_missing_key(self, required_key):
        data = make_md_dict()
        data[required_key] = None
        lines = make_lines(**data)
        with pytest.raises(se.MetaDataKeyNotFoundError):
            volume.VolumeMetadata.from_lines(lines)

    def test_from_lines_invalid_param(self):
        lines = make_lines(INVALID_KEY='foo')
        md = volume.VolumeMetadata.from_lines(lines)
        with pytest.raises(KeyError):
            md["INVALID_KEY"]

    @pytest.mark.parametrize("key", [sc.CTIME, sc.CAPACITY])
    def test_from_lines_int_parse_error(self, key):
        lines = make_lines(**{key: 'not_an_integer'})
        with pytest.raises(ValueError):
            volume.VolumeMetadata.from_lines(lines)

    @pytest.mark.parametrize("version", [4, 5])
    def test_from_lines_common(self, monkeypatch, version):
        data = make_init_params()
        monkeypatch.setattr(time, 'time', lambda: FAKE_TIME)
        md = volume.VolumeMetadata(**data)
        lines = md.storage_format(version).splitlines()

        md = volume.VolumeMetadata.from_lines(lines)
        assert data['domain'] == md.domain
        assert data['image'] == md.image
        assert data['puuid'] == md.puuid
        assert data['format'] == md.format
        assert data['type'] == md.type
        assert data['voltype'] == md.voltype
        assert str(data['disktype']) == md.disktype
        assert data['description'] == md.description
        assert FAKE_TIME == md.ctime
        assert data['legality'] == md.legality
        assert int(data['generation']) == md.generation

    def test_from_lines_v5(self):
        data = make_init_params()
        md = volume.VolumeMetadata(**data)
        lines = md.storage_format(5).splitlines()

        md = volume.VolumeMetadata.from_lines(lines)
        assert int(data['capacity']) == md.capacity

    def test_from_lines_v4(self):
        data = make_init_params()
        md = volume.VolumeMetadata(**data)
        lines = md.storage_format(5).splitlines()
        lines.remove(b"CAP=1073741824")
        lines.insert(0, b"SIZE=4096")

        md = volume.VolumeMetadata.from_lines(lines)
        assert md.capacity == 2 * MB

    def test_from_lines_no_size_and_capacity(self):
        data = make_init_params()
        md = volume.VolumeMetadata(**data)
        lines = md.storage_format(5).splitlines()
        lines.remove(b"CAP=1073741824")

        with pytest.raises(se.MetaDataKeyNotFoundError):
            volume.VolumeMetadata.from_lines(lines)

    def test_generation_default(self):
        lines = make_lines(GEN=None)
        md = volume.VolumeMetadata.from_lines(lines)
        assert sc.DEFAULT_GENERATION == md.generation

    @pytest.mark.xfail(six.PY3, reason="bytes handled incorrectly")
    def test_cleared_metadata(self):
        lines = CLEARED_VOLUME_METADATA.rstrip(b"\0").splitlines()
        with pytest.raises(se.MetadataCleared):
            volume.VolumeMetadata.from_lines(lines)

    def test_empty_metadata(self):
        with pytest.raises(se.MetaDataKeyNotFoundError):
            volume.VolumeMetadata.from_lines([])


class TestMDSize:
    MAX_DESCRIPTION = "x" * sc.DESCRIPTION_SIZE
    # We don't think that any one will actually preallocate
    # 1 PB in near future.
    MAX_PREALLOCATED_SIZE = 1024**5
    MAX_VOLUME_SIZE = 2**63 - 1

    @pytest.mark.parametrize('size', [
        sc.DESCRIPTION_SIZE,
        sc.DESCRIPTION_SIZE + 1
    ])
    def test_long_description(self, size):
        params = make_init_params(description="!" * size)
        md = volume.VolumeMetadata(**params)
        assert sc.DESCRIPTION_SIZE == len(md.description)

    @pytest.mark.parametrize('version', [4, 5])
    @pytest.mark.parametrize('md_params', [
        # Preallocated block/file example:
        #
        # CTIME=1542308390
        # FORMAT=RAW
        # DISKTYPE=ISOF
        # LEGALITY=ILLEGAL
        # CAP=1125899906842624
        # VOLTYPE=LEAF
        # DESCRIPTION={"DiskAlias":"Fedora-Server-dvd-x86_64-29-1.2.iso", "DiskDescription":"Uploaded disk"} # NOQA: E501 (potentially long line)
        # IMAGE=bc9d15fa-70eb-40aa-8a2e-e4f27664752f
        # PUUID=00000000-0000-0000-0000-000000000000
        # MTIME=0
        # POOL_UUID=
        # TYPE=PREALLOCATED
        # GEN=0
        # EOF
        {
            'capacity': MAX_PREALLOCATED_SIZE,
            'type': 'PREALLOCATED'
        },
        # Sparse block/file example:
        #
        # CTIME=1542308390
        # FORMAT=RAW
        # DISKTYPE=ISOF
        # LEGALITY=ILLEGAL
        # CAP=9223372036854775808
        # VOLTYPE=LEAF
        # DESCRIPTION={"DiskAlias":"Fedora-Server-dvd-x86_64-29-1.2.iso", "DiskDescription":"Uploaded disk"} # NOQA: E501 (potentially long line)
        # IMAGE=bc9d15fa-70eb-40aa-8a2e-e4f27664752f
        # PUUID=00000000-0000-0000-0000-000000000000
        # MTIME=0
        # POOL_UUID=
        # TYPE=SPARSE
        # GEN=0
        # EOF
        {
            'capacity': MAX_VOLUME_SIZE,
            'type': 'SPARSE'
        }
    ])
    def test_max_size(self, version, md_params):
        md = volume.VolumeMetadata(
            ctime=1440935038,
            description=self.MAX_DESCRIPTION,
            disktype="ISOF",
            domain='75f8a1bb-4504-4314-91ca-d9365a30692b',
            format="RAW",
            generation=sc.MAX_GENERATION,
            image='75f8a1bb-4504-4314-91ca-d9365a30692b',
            legality='ILLEGAL',
            # Blank UUID for RAW, can be real UUID for COW.
            puuid=sc.BLANK_UUID,
            capacity=md_params['capacity'],
            type=md_params['type'],
            voltype='INTERNAL',
        )

        md_len = len(md.storage_format(version))
        # Needed for documenting sc.MAX_DESCRIPTION.
        md_fields = md_len - sc.DESCRIPTION_SIZE
        md_free = sc.METADATA_SIZE - md_len

        # To see this, run:
        # tox -e storage-py27 tests/storage/volume_metadata_test.py -- -vs
        print("version={} type={} length={} fields={} free={}"
              .format(version, md_params['type'], md_len, md_fields, md_free))

        assert sc.METADATA_SIZE >= md_len

    def test_capacity_integer(self):
        params = make_init_params()
        md = volume.VolumeMetadata(**params)
        with pytest.raises(AssertionError):
            md.capacity = "fail"

    def test_ctime_integer(self):
        params = make_init_params()
        md = volume.VolumeMetadata(**params)
        with pytest.raises(AssertionError):
            md.ctime = "fail"

    def test_generation_integer(self):
        params = make_init_params()
        md = volume.VolumeMetadata(**params)
        with pytest.raises(AssertionError):
            md.generation = "fail"


class TestDictInterface:

    @pytest.mark.parametrize('size', [
        sc.DESCRIPTION_SIZE,
        sc.DESCRIPTION_SIZE + 1
    ])
    def test_description_trunc(self, size):
        params = make_init_params()
        md = volume.VolumeMetadata(**params)
        md[sc.DESCRIPTION] = "!" * size
        assert sc.DESCRIPTION_SIZE == len(md[sc.DESCRIPTION])

    def test_dict_interface(self):
        params = make_init_params()
        md = volume.VolumeMetadata(**params)
        assert md[sc.DESCRIPTION] == params['description']
        assert md[sc.DISKTYPE] == params['disktype']
        assert md[sc.DOMAIN] == params['domain']
        assert md[sc.FORMAT] == params['format']
        assert md[sc.IMAGE] == params['image']
        assert md[sc.PUUID] == params['puuid']
        assert md[sc.CAPACITY] == str(params['capacity'])
        assert md[sc.TYPE] == params['type']
        assert md[sc.VOLTYPE] == params['voltype']
        assert md[sc.DISKTYPE] == params['disktype']
        assert md[sc.GENERATION] == params['generation']

    def test_dict_setter(self):
        params = make_init_params()
        md = volume.VolumeMetadata(**params)
        assert md[sc.DESCRIPTION] == params['description']
        md[sc.DESCRIPTION] = "New description"
        assert "New description" == md[sc.DESCRIPTION]

    def test_get_nonexistent(self):
        params = make_init_params()
        md = volume.VolumeMetadata(**params)
        with pytest.raises(KeyError):
            md["INVALID_KEY"]

    def test_set_nonexistent(self):
        params = make_init_params()
        md = volume.VolumeMetadata(**params)
        with pytest.raises(KeyError):
            md["INVALID_KEY"] = "VALUE"

    def test_get_default(self):
        params = make_init_params()
        md = volume.VolumeMetadata(**params)
        assert md.get("INVALID_KEY", "TEST") == "TEST"
