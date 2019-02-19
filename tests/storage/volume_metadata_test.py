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
from vdsm.storage import image
from vdsm.storage import volume


MB = 1024 ** 2
FAKE_TIME = 1461095629


def make_init_params(**kwargs):
    res = dict(
        domain=make_uuid(),
        image=make_uuid(),
        puuid=make_uuid(),
        size=1024 * MB,
        format=sc.type2name(sc.RAW_FORMAT),
        type=sc.type2name(sc.SPARSE_VOL),
        voltype=sc.type2name(sc.LEAF_VOL),
        disktype=image.SYSTEM_DISK_TYPE,
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
        sc.SIZE: '0',
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
    lines = ['EOF']
    for k, v in data.items():
        if v is not None:
            lines.insert(0, "%s=%s" % (k, v))
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
            SIZE=str(params['size']),
            TYPE=params['type'],
            VOLTYPE=params['voltype'],
            GEN=params['generation'])

        monkeypatch.setattr(time, 'time', lambda: FAKE_TIME)
        info = volume.VolumeMetadata(**params)
        for key, value in six.iteritems(expected):
            assert info[key] == value

    def test_storage_format_v4(self):
        params = make_init_params(ctime=FAKE_TIME)
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
            """ % params)
        md = volume.VolumeMetadata(**params)
        assert expected == md.storage_format(4)

    def test_storage_format_v5(self):
        params = make_init_params(ctime=FAKE_TIME)
        expected = textwrap.dedent("""\
            CTIME=%(ctime)s
            DESCRIPTION=%(description)s
            DISKTYPE=%(disktype)s
            DOMAIN=%(domain)s
            FORMAT=%(format)s
            GEN=%(generation)s
            IMAGE=%(image)s
            LEGALITY=%(legality)s
            PUUID=%(puuid)s
            SIZE=%(size)s
            TYPE=%(type)s
            VOLTYPE=%(voltype)s
            EOF
            """ % params)
        md = volume.VolumeMetadata(**params)
        assert expected == md.storage_format(5)

    @pytest.mark.parametrize("param", ['size', 'ctime'])
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

    @pytest.mark.parametrize("key", [sc.SIZE, sc.CTIME])
    def test_from_lines_int_parse_error(self, key):
        lines = make_lines(**{key: 'not_an_integer'})
        with pytest.raises(ValueError):
            volume.VolumeMetadata.from_lines(lines)

    def test_from_lines(self):
        data = make_md_dict()
        lines = make_lines(**data)

        md = volume.VolumeMetadata.from_lines(lines)
        assert data[sc.DOMAIN] == md.domain
        assert data[sc.IMAGE] == md.image
        assert data[sc.PUUID] == md.puuid
        assert int(data[sc.SIZE]) == md.size
        assert data[sc.FORMAT] == md.format
        assert data[sc.TYPE] == md.type
        assert data[sc.VOLTYPE] == md.voltype
        assert data[sc.DISKTYPE] == md.disktype
        assert data[sc.DESCRIPTION] == md.description
        assert int(data[sc.CTIME]) == md.ctime
        assert data[sc.LEGALITY] == md.legality
        assert int(data[sc.GENERATION]) == md.generation

    def test_generation_default(self):
        lines = make_lines(GEN=None)
        md = volume.VolumeMetadata.from_lines(lines)
        assert sc.DEFAULT_GENERATION == md.generation


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
        # SIZE=2199023255552
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
            'size': MAX_PREALLOCATED_SIZE,
            'type': 'PREALLOCATED'
        },
        # Sparse block/file example:
        #
        # CTIME=1542308390
        # FORMAT=RAW
        # DISKTYPE=ISOF
        # LEGALITY=ILLEGAL
        # SIZE=18014398509481984
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
            'size': MAX_VOLUME_SIZE,
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
            size=md_params['size'] // 512,
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
        assert md[sc.SIZE] == str(params['size'])
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
