#
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager

from fakelib import FakeNotifier
from fakelib import FakeScheduler
from monkeypatch import MonkeyPatchScope

from storage.storagefakelib import (
    FakeResourceManager,
    fake_guarded_context,
)

from storage.storagetestlib import (
    fake_env,
    make_qemu_chain,
)

from testlib import make_uuid
from testlib import VdsmTestCase, expandPermutations, permutations

from vdsm import jobs
from vdsm.storage import blockVolume
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage.sdm.api import copy_data, update_volume


@expandPermutations
class TestUpdateVolume(VdsmTestCase):
    DEFAULT_SIZE = 1048576

    def setUp(self):
        self.scheduler = FakeScheduler()
        self.notifier = FakeNotifier()
        jobs.start(self.scheduler, self.notifier)

    def tearDown(self):
        jobs._clear()

    @contextmanager
    def make_env(self, storage_type, fmt=sc.name2type('cow'), chain_length=1,
                 size=DEFAULT_SIZE, qcow2_compat='0.10'):
        with fake_env(storage_type, sd_version=4) as env:
            rm = FakeResourceManager()
            with MonkeyPatchScope([
                (guarded, 'context', fake_guarded_context()),
                (copy_data, 'sdCache', env.sdcache),
                (blockVolume, 'rm', rm),
            ]):
                env.chain = make_qemu_chain(env, size, fmt, chain_length,
                                            qcow2_compat=qcow2_compat)
                yield env

    @permutations([
        ('file', sc.LEGAL_VOL, sc.ILLEGAL_VOL),
        ('file', sc.ILLEGAL_VOL, sc.LEGAL_VOL),
        ('block', sc.LEGAL_VOL, sc.ILLEGAL_VOL),
        ('block', sc.ILLEGAL_VOL, sc.LEGAL_VOL),
    ])
    def test_set_legality(self, env_type, legality, expected):
        with self.make_env(env_type) as env:
            vol = env.chain[0]
            vol.setLegality(legality)
            generation = vol.getMetaParam(sc.GENERATION)
            job = update_volume.Job(make_uuid(), 0,
                                    make_endpoint_from_volume(vol),
                                    dict(legality=expected))
            job.run()
            self.assertEqual(jobs.STATUS.DONE, job.status)
            self.assertEqual(expected, vol.getMetaParam(sc.LEGALITY))
            self.assertEqual(generation + 1,
                             vol.getMetaParam(sc.GENERATION))

    @permutations([
        ('file', sc.LEGAL_VOL),
        ('file', sc.ILLEGAL_VOL),
        ('block', sc.ILLEGAL_VOL),
        ('block', sc.LEGAL_VOL),
    ])
    def test_set_legality_invalid(self, env_type, legality):
        with self.make_env(env_type) as env:
            vol = env.chain[0]
            vol.setLegality(legality)
            generation = vol.getMetaParam(sc.GENERATION)
            job = update_volume.Job(make_uuid(), 0,
                                    make_endpoint_from_volume(vol),
                                    dict(legality=legality))
            job.run()
            self.assertEqual(job.status, jobs.STATUS.FAILED)
            self.assertEqual(type(job.error), se.InvalidVolumeUpdate)
            self.assertEqual(generation,
                             vol.getMetaParam(sc.GENERATION))

    @permutations([('file',), ('block',)])
    def test_set_description(self, env_type):
        with self.make_env(env_type) as env:
            vol = env.chain[0]
            generation = vol.getMetaParam(sc.GENERATION)
            description = 'my wonderful description'
            job = update_volume.Job(make_uuid(), 0,
                                    make_endpoint_from_volume(vol),
                                    dict(description=description))
            job.run()
            self.assertEqual(jobs.STATUS.DONE, job.status)
            self.assertEqual(description,
                             vol.getMetaParam(sc.DESCRIPTION))
            self.assertEqual(generation + 1,
                             vol.getMetaParam(sc.GENERATION))

    @permutations([('file',), ('block',)])
    def test_set_type(self, env_type):
        with self.make_env(env_type) as env:
            leaf_vol = env.chain[0]
            generation = leaf_vol.getMetaParam(sc.GENERATION)
            job = update_volume.Job(make_uuid(), 0,
                                    make_endpoint_from_volume(leaf_vol),
                                    dict(type=sc.type2name(sc.SHARED_VOL)))
            job.run()
            self.assertEqual(jobs.STATUS.DONE, job.status)
            self.assertEqual(sc.type2name(sc.SHARED_VOL),
                             leaf_vol.getMetaParam(sc.VOLTYPE))
            self.assertEqual(generation + 1,
                             leaf_vol.getMetaParam(sc.GENERATION))

    @permutations([('file',), ('block',)])
    def test_set_type_leaf_with_parent(self, env_type):
        with self.make_env(env_type, chain_length=2) as env:
            top_vol = env.chain[1]
            generation = top_vol.getMetaParam(sc.GENERATION)
            job = update_volume.Job(make_uuid(), 0,
                                    make_endpoint_from_volume(top_vol),
                                    dict(type=sc.type2name(sc.SHARED_VOL)))
            job.run()
            self.assertEqual(job.status, jobs.STATUS.FAILED)
            self.assertEqual(type(job.error), se.InvalidVolumeUpdate)
            self.assertEqual(sc.type2name(sc.LEAF_VOL),
                             top_vol.getMetaParam(sc.VOLTYPE))
            self.assertEqual(generation,
                             top_vol.getMetaParam(sc.GENERATION))

    @permutations([('file',), ('block',)])
    def test_set_type_already_shared(self, env_type):
        with self.make_env(env_type, chain_length=1) as env:
            shared_vol = env.chain[0]
            generation = shared_vol.getMetaParam(sc.GENERATION)
            shared_vol.setShared()
            job = update_volume.Job(make_uuid(), 0,
                                    make_endpoint_from_volume(shared_vol),
                                    dict(type=sc.type2name(sc.SHARED_VOL)))
            job.run()
            self.assertEqual(job.status, jobs.STATUS.FAILED)
            self.assertEqual(type(job.error), se.InvalidVolumeUpdate)
            self.assertEqual(generation,
                             shared_vol.getMetaParam(sc.GENERATION))

    @permutations([('file',), ('block',)])
    def test_set_type_internal(self, env_type):
        with self.make_env(env_type, chain_length=1) as env:
            internal_vol = env.chain[0]
            generation = internal_vol.getMetaParam(sc.GENERATION)
            internal_vol.setInternal()
            job = update_volume.Job(make_uuid(), 0,
                                    make_endpoint_from_volume(internal_vol),
                                    dict(type=sc.type2name(sc.SHARED_VOL)))
            job.run()
            self.assertEqual(job.status, jobs.STATUS.FAILED)
            self.assertEqual(type(job.error), se.InvalidVolumeUpdate)
            self.assertEqual(generation,
                             internal_vol.getMetaParam(sc.GENERATION))

    @permutations([('file',), ('block',)])
    def test_set_generation(self, env_type):
        with self.make_env(env_type) as env:
            vol = env.chain[0]
            job = update_volume.Job(make_uuid(), 0,
                                    make_endpoint_from_volume(vol),
                                    dict(generation=44))
            job.run()
            self.assertEqual(jobs.STATUS.DONE, job.status)
            self.assertEqual(44, vol.getMetaParam(sc.GENERATION))


@expandPermutations
class TestValidation(VdsmTestCase):

    def test_empty_vol_attr(self):
        with self.assertRaises(ValueError):
            update_volume.Job(make_uuid(), 0, make_endpoint(), {})

    def test_invalid_legality(self):
        with self.assertRaises(ValueError):
            update_volume.Job(make_uuid(), 0, make_endpoint(),
                              dict(legality='INVALID'))

    @permutations([
        (sc.type2name(sc.INTERNAL_VOL),),
        (sc.type2name(sc.LEAF_VOL),),
    ])
    def test_invalid_type_internal(self, type):
        with self.assertRaises(ValueError):
            update_volume.Job(make_uuid(), 0, make_endpoint(),
                              dict(type=type))

    @permutations([
        (-1,),
        (1000,),
        ("not_an_integer",),
    ])
    def test_invalid_generation(self, generation):
        with self.assertRaises(ValueError):
            update_volume.Job(make_uuid(), 0, make_endpoint(),
                              dict(generation=generation))


def make_endpoint_from_volume(vol):
    return make_endpoint(vol.sdUUID, vol.imgUUID, vol.volUUID)


def make_endpoint(sd_id=make_uuid(), img_id=make_uuid(),
                  vol_id=make_uuid()):
    return dict(sd_id=sd_id, img_id=img_id, vol_id=vol_id, generation=0)
