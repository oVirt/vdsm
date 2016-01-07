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

from storage import image, volume


MB = 1024 ** 2
FAKE_TIME = 1461095629


def make_init_params(**kwargs):
    res = dict(
        domain=str(uuid.uuid4()),
        image=str(uuid.uuid4()),
        puuid=str(uuid.uuid4()),
        size=1024 * MB,
        format=volume.type2name(volume.RAW_FORMAT),
        type=volume.type2name(volume.SPARSE_VOL),
        voltype=volume.type2name(volume.LEAF_VOL),
        disktype=image.SYSTEM_DISK_TYPE,
        description="",
        legality=volume.LEGAL_VOL)
    res.update(kwargs)
    return res


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
