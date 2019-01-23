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

import textwrap
import time

import pytest

from testlib import make_uuid
from monkeypatch import MonkeyPatchScope

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
        sc.MTIME: '0',
        sc.CTIME: '0',
        sc.POOL: '',
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

    def test_create_info(self):
        params = make_init_params()
        expected = dict(
            CTIME=str(FAKE_TIME),
            DESCRIPTION=params['description'],
            DISKTYPE=params['disktype'],
            DOMAIN=params['domain'],
            FORMAT=params['format'],
            IMAGE=params['image'],
            LEGALITY=params['legality'],
            MTIME="0",
            POOL_UUID="",
            PUUID=params['puuid'],
            SIZE=str(params['size']),
            TYPE=params['type'],
            VOLTYPE=params['voltype'],
            GEN=params['generation'])

        with MonkeyPatchScope([[time, 'time', lambda: FAKE_TIME]]):
            info = volume.VolumeMetadata(**params).legacy_info()
            assert expected == info

    def test_storage_format(self):
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
            POOL_UUID=
            PUUID=%(puuid)s
            SIZE=%(size)s
            TYPE=%(type)s
            VOLTYPE=%(voltype)s
            EOF
            """ % params)
        md = volume.VolumeMetadata(**params)
        assert expected == md.storage_format()

    @pytest.mark.parametrize('size', [
        sc.DESCRIPTION_SIZE,
        sc.DESCRIPTION_SIZE + 1
    ])
    def test_long_description(self, size):
        params = make_init_params(description="!" * size)
        md = volume.VolumeMetadata(**params)
        assert sc.DESCRIPTION_SIZE == len(md.description)

    @pytest.mark.parametrize("param", ['size', 'ctime', 'mtime'])
    def test_int_params_str_raises(self, param):
        params = make_init_params(**{param: 'not_an_int'})
        with pytest.raises(AssertionError):
            volume.VolumeMetadata(**params)

    @pytest.mark.parametrize("required_key",
                             [key for key in make_md_dict()
                              if key not in (sc.POOL, sc.GENERATION)])
    def test_from_lines_missing_key(self, required_key):
        data = make_md_dict(POOL=None)
        data[required_key] = None
        lines = make_lines(**data)
        with pytest.raises(se.MetaDataKeyNotFoundError):
            volume.VolumeMetadata.from_lines(lines)

    @pytest.mark.parametrize("val", [None, 'pool'])
    def test_deprecated_pool(self, val):
        lines = make_lines(**{sc.POOL: val})
        md = volume.VolumeMetadata.from_lines(lines)
        assert "" == md.legacy_info()[sc.POOL]

    def test_from_lines_invalid_param(self):
        lines = make_lines(INVALID_KEY='foo')
        assert ("INVALID_KEY" not in
                volume.VolumeMetadata.from_lines(lines).legacy_info())

    @pytest.mark.parametrize("key", [sc.SIZE, sc.CTIME, sc.MTIME])
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
        assert int(data[sc.MTIME]) == md.mtime
        assert int(data[sc.CTIME]) == md.ctime
        assert data[sc.LEGALITY] == md.legality
        assert int(data[sc.GENERATION]) == md.generation

    def test_generation_default(self):
        lines = make_lines(GEN=None)
        md = volume.VolumeMetadata.from_lines(lines)
        assert sc.DEFAULT_GENERATION == md.generation
