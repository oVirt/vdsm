# Copyright 2016 Red Hat, Inc.
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

import textwrap
import time
import uuid

from testlib import VdsmTestCase, permutations, expandPermutations
from monkeypatch import MonkeyPatchScope

from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from storage import image, volume


MB = 1024 ** 2
FAKE_TIME = 1461095629


def make_init_params(**kwargs):
    res = dict(
        domain=str(uuid.uuid4()),
        image=str(uuid.uuid4()),
        puuid=str(uuid.uuid4()),
        size=1024 * MB,
        format=sc.type2name(sc.RAW_FORMAT),
        type=sc.type2name(sc.SPARSE_VOL),
        voltype=sc.type2name(sc.LEAF_VOL),
        disktype=image.SYSTEM_DISK_TYPE,
        description="",
        legality=sc.LEGAL_VOL)
    res.update(kwargs)
    return res


def make_md_dict(**kwargs):
    res = {
        volume.DOMAIN: 'domain',
        volume.IMAGE: 'image',
        volume.PUUID: 'parent',
        volume.SIZE: '0',
        volume.FORMAT: 'format',
        volume.TYPE: 'type',
        volume.VOLTYPE: 'voltype',
        volume.DISKTYPE: 'disktype',
        volume.DESCRIPTION: 'description',
        volume.LEGALITY: 'legality',
        volume.MTIME: '0',
        volume.CTIME: '0',
        volume.POOL: '',
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


@expandPermutations
class VolumeMetadataTests(VdsmTestCase):

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
            VOLTYPE=params['voltype'])

        with MonkeyPatchScope([[time, 'time', lambda: FAKE_TIME]]):
            info = volume.VolumeMetadata(**params).legacy_info()
            self.assertEqual(expected, info)

    def test_storage_format(self):
        params = make_init_params(ctime=FAKE_TIME)
        expected = textwrap.dedent("""\
            CTIME=%(ctime)s
            DESCRIPTION=%(description)s
            DISKTYPE=%(disktype)s
            DOMAIN=%(domain)s
            FORMAT=%(format)s
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
        self.assertEqual(expected, md.storage_format())

    @permutations([
        [volume.DESCRIPTION_SIZE],
        [volume.DESCRIPTION_SIZE + 1]
    ])
    def test_long_description(self, size):
        params = make_init_params(description="!" * size)
        md = volume.VolumeMetadata(**params)
        self.assertEqual(volume.DESCRIPTION_SIZE, len(md.description))

    @permutations([['size'], ['ctime'], ['mtime']])
    def test_int_params_str_raises(self, param):
        params = make_init_params(**{param: 'not_an_int'})
        self.assertRaises(AssertionError, volume.VolumeMetadata, **params)

    @permutations([[key] for key in make_md_dict() if key != volume.POOL])
    def test_from_lines_missing_key(self, required_key):
        data = make_md_dict(CTIME=None, MTIME=None, POOL=None)
        data[required_key] = None
        lines = make_lines(**data)
        self.assertRaises(se.MetaDataKeyNotFoundError,
                          volume.VolumeMetadata.from_lines, lines)

    @permutations([[None], ['pool']])
    def test_deprecated_pool(self, val):
        lines = make_lines(**{volume.POOL: val})
        md = volume.VolumeMetadata.from_lines(lines)
        self.assertEqual("", md.legacy_info()[volume.POOL])

    def test_from_lines_invalid_param(self):
        lines = make_lines(INVALID_KEY='foo')
        self.assertNotIn("INVALID_KEY",
                         volume.VolumeMetadata.from_lines(lines).legacy_info())

    @permutations([[volume.SIZE], [volume.CTIME], [volume.MTIME]])
    def test_from_lines_int_parse_error(self, key):
        lines = make_lines(**{key: 'not_an_integer'})
        self.assertRaises(ValueError,
                          volume.VolumeMetadata.from_lines, lines)

    def test_from_lines(self):
        data = make_md_dict()
        lines = make_lines(**data)

        md = volume.VolumeMetadata.from_lines(lines)
        self.assertEqual(data[volume.DOMAIN], md.domain)
        self.assertEqual(data[volume.IMAGE], md.image)
        self.assertEqual(data[volume.PUUID], md.puuid)
        self.assertEqual(int(data[volume.SIZE]), md.size)
        self.assertEqual(data[volume.FORMAT], md.format)
        self.assertEqual(data[volume.TYPE], md.type)
        self.assertEqual(data[volume.VOLTYPE], md.voltype)
        self.assertEqual(data[volume.DISKTYPE], md.disktype)
        self.assertEqual(data[volume.DESCRIPTION], md.description)
        self.assertEqual(int(data[volume.MTIME]), md.mtime)
        self.assertEqual(int(data[volume.CTIME]), md.ctime)
        self.assertEqual(data[volume.LEGALITY], md.legality)
