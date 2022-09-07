# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from storage.storagetestlib import FakeGuardedLock
from testlib import VdsmTestCase

from vdsm.storage import guarded


class InjectedFailure(Exception):
    pass


class TestContext(VdsmTestCase):

    def test_empty(self):
        with guarded.context([]):
            pass

    def test_one_vol(self):
        log = []
        locks = [
            FakeGuardedLock('01_dom', 'dom', 'mode', log),
            FakeGuardedLock('02_img', 'img', 'mode', log),
            FakeGuardedLock('03_vol', 'vol', 'mode', log)]
        expected = [
            ('acquire', '01_dom', 'dom', 'mode'),
            ('acquire', '02_img', 'img', 'mode'),
            ('acquire', '03_vol', 'vol', 'mode'),
            ('release', '03_vol', 'vol', 'mode'),
            ('release', '02_img', 'img', 'mode'),
            ('release', '01_dom', 'dom', 'mode')]
        with guarded.context(locks):
            self.assertEqual(expected[:3], log)
        self.assertEqual(expected, log)

    def test_two_vols_different_domains(self):
        log = []
        locks = [
            FakeGuardedLock('01_dom', 'dom1', 'mode', log),
            FakeGuardedLock('02_img', 'img1', 'mode', log),
            FakeGuardedLock('03_vol', 'vol1', 'mode', log),
            FakeGuardedLock('01_dom', 'dom2', 'mode', log),
            FakeGuardedLock('02_img', 'img2', 'mode', log),
            FakeGuardedLock('03_vol', 'vol2', 'mode', log)]
        expected = [
            ('acquire', '01_dom', 'dom1', 'mode'),
            ('acquire', '01_dom', 'dom2', 'mode'),
            ('acquire', '02_img', 'img1', 'mode'),
            ('acquire', '02_img', 'img2', 'mode'),
            ('acquire', '03_vol', 'vol1', 'mode'),
            ('acquire', '03_vol', 'vol2', 'mode'),
            ('release', '03_vol', 'vol2', 'mode'),
            ('release', '03_vol', 'vol1', 'mode'),
            ('release', '02_img', 'img2', 'mode'),
            ('release', '02_img', 'img1', 'mode'),
            ('release', '01_dom', 'dom2', 'mode'),
            ('release', '01_dom', 'dom1', 'mode')]
        with guarded.context(locks):
            self.assertEqual(expected[:6], log)
        self.assertEqual(expected, log)

    def test_two_vols_same_image(self):
        log = []
        locks = [
            FakeGuardedLock('01_dom', 'dom1', 'mode', log),
            FakeGuardedLock('02_img', 'img1', 'mode', log),
            FakeGuardedLock('03_vol', 'vol1', 'mode', log),
            FakeGuardedLock('01_dom', 'dom1', 'mode', log),
            FakeGuardedLock('02_img', 'img1', 'mode', log),
            FakeGuardedLock('03_vol', 'vol2', 'mode', log)]
        expected = [
            ('acquire', '01_dom', 'dom1', 'mode'),
            ('acquire', '02_img', 'img1', 'mode'),
            ('acquire', '03_vol', 'vol1', 'mode'),
            ('acquire', '03_vol', 'vol2', 'mode'),
            ('release', '03_vol', 'vol2', 'mode'),
            ('release', '03_vol', 'vol1', 'mode'),
            ('release', '02_img', 'img1', 'mode'),
            ('release', '01_dom', 'dom1', 'mode')]
        with guarded.context(locks):
            self.assertEqual(expected[:4], log)
        self.assertEqual(expected, log)

    def test_acquire_failure(self):
        log = []
        locks = [
            FakeGuardedLock('01_dom', 'dom1', 'mode', log),
            FakeGuardedLock('02_img', 'img1', 'mode', log),
            FakeGuardedLock('03_vol', 'vol1', 'mode', log,
                            acquire=InjectedFailure)]
        expected = [
            ('acquire', '01_dom', 'dom1', 'mode'),
            ('acquire', '02_img', 'img1', 'mode'),
            ('release', '02_img', 'img1', 'mode'),
            ('release', '01_dom', 'dom1', 'mode')]
        with self.assertRaises(InjectedFailure):
            with guarded.context(locks):
                pass
        self.assertEqual(expected, log)

    def test_aquire_failure_then_release_failure(self):
        log = []
        locks = [
            FakeGuardedLock('01_dom', 'dom1', 'mode', log),
            FakeGuardedLock('02_img', 'img1', 'mode', log,
                            release=InjectedFailure),
            FakeGuardedLock('03_vol', 'vol1', 'mode', log,
                            acquire=InjectedFailure)]
        expected = [
            ('acquire', '01_dom', 'dom1', 'mode'),
            ('acquire', '02_img', 'img1', 'mode'),
            ('release', '01_dom', 'dom1', 'mode')]
        with self.assertRaises(InjectedFailure):
            with guarded.context(locks):
                pass
        self.assertEqual(expected, log)

    def test_release_failure(self):
        log = []
        locks = [
            FakeGuardedLock('01_dom', 'dom1', 'mode', log),
            FakeGuardedLock('02_img', 'img1', 'mode', log),
            FakeGuardedLock('03_vol', 'vol1', 'mode', log,
                            release=InjectedFailure)]
        expected = [
            ('acquire', '01_dom', 'dom1', 'mode'),
            ('acquire', '02_img', 'img1', 'mode'),
            ('acquire', '03_vol', 'vol1', 'mode'),
            ('release', '02_img', 'img1', 'mode'),
            ('release', '01_dom', 'dom1', 'mode')]
        with self.assertRaises(guarded.ReleaseError):
            with guarded.context(locks):
                pass
        self.assertEqual(expected, log)

    def test_fail_inside_context(self):
        log = []
        locks = [
            FakeGuardedLock('01_dom', 'dom1', 'mode', log),
            FakeGuardedLock('02_img', 'img1', 'mode', log),
            FakeGuardedLock('03_vol', 'vol1', 'mode', log)]
        expected = [
            ('acquire', '01_dom', 'dom1', 'mode'),
            ('acquire', '02_img', 'img1', 'mode'),
            ('acquire', '03_vol', 'vol1', 'mode'),
            ('release', '03_vol', 'vol1', 'mode'),
            ('release', '02_img', 'img1', 'mode'),
            ('release', '01_dom', 'dom1', 'mode')]
        with self.assertRaises(InjectedFailure):
            with guarded.context(locks):
                raise InjectedFailure()
        self.assertEqual(expected, log)

    def test_fail_inside_context_with_release_failure(self):
        log = []
        locks = [
            FakeGuardedLock('01_dom', 'dom1', 'mode', log),
            FakeGuardedLock('02_img', 'img1', 'mode', log),
            FakeGuardedLock('03_vol', 'vol1', 'mode', log,
                            release=InjectedFailure)]
        expected = [
            ('acquire', '01_dom', 'dom1', 'mode'),
            ('acquire', '02_img', 'img1', 'mode'),
            ('acquire', '03_vol', 'vol1', 'mode'),
            ('release', '02_img', 'img1', 'mode'),
            ('release', '01_dom', 'dom1', 'mode')]
        with self.assertRaises(RuntimeError):
            with guarded.context(locks):
                raise RuntimeError()
        self.assertEqual(expected, log)

    def test_deadlock(self):
        log = []
        locks = [
            FakeGuardedLock('00_storage', 'dom', 'shared', log),
            # Attemting to lock next locks will deadlock in resourceManager.
            FakeGuardedLock('02_img.dom', 'img', 'exclusive', log),
            FakeGuardedLock('02_img.dom', 'img', 'shared', log),
            FakeGuardedLock('03_vol.dom', 'vol', 'exclusive', log),
        ]
        # Creating a context should raise
        with self.assertRaises(guarded.Deadlock):
            with guarded.context(locks):
                pass
        # Without locking any of the locks
        self.assertEqual([], log)
