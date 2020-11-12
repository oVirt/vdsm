#
# Copyright 2017-2020 Red Hat, Inc.
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

from testlib import VdsmTestCase

from vdsm.virt.utils import prepared
from vdsm.virt.utils import TeardownError
import pytest


class FakeImage(object):
    def __init__(self, name, log, prepare=None, teardown=None):
        self._name = name
        self._log = log
        self._prepare_err = prepare
        self._teardown_err = teardown

    @property
    def name(self):
        return self._name

    def prepare(self):
        if self._prepare_err:
            raise self._prepare_err()
        entry = ('prepare', self._name)
        self._log.append(entry)

    def teardown(self):
        if self._teardown_err:
            raise self._teardown_err()
        entry = ('teardown', self._name)
        self._log.append(entry)


class InjectedFailure(Exception):
    pass


class ContextTest(VdsmTestCase):

    def test_empty(self):
        with self.assertNotRaises():
            with prepared([]):
                pass

    def test_one_image(self):
        log = []
        images = [
            FakeImage('img', log)]
        expected = [
            ('prepare', 'img'),
            ('teardown', 'img')]
        with prepared(images):
            assert expected[:1] == log
        assert expected == log

    def test_two_images(self):
        log = []
        images = [
            FakeImage('img1', log),
            FakeImage('img2', log)]
        expected = [
            ('prepare', 'img1'),
            ('prepare', 'img2'),
            ('teardown', 'img2'),
            ('teardown', 'img1')]
        with prepared(images):
            assert expected[:2] == log
        assert expected == log

    def test_prepare_failure(self):
        log = []
        images = [
            FakeImage('img1', log),
            FakeImage('img2', log,
                      prepare=InjectedFailure)]
        expected = [
            ('prepare', 'img1'),
            ('teardown', 'img1')]
        with pytest.raises(InjectedFailure):
            with prepared(images):
                pass
        assert expected == log

    def test_prepare_failure_then_teardown_failure(self):
        log = []
        images = [
            FakeImage('img1', log),
            FakeImage('img2', log,
                      teardown=InjectedFailure),
            FakeImage('img3', log,
                      prepare=InjectedFailure)]
        expected = [
            ('prepare', 'img1'),
            ('prepare', 'img2'),
            ('teardown', 'img1')]
        with pytest.raises(InjectedFailure):
            with prepared(images):
                pass
        assert expected == log

    def test_teardown_failure(self):
        log = []
        images = [
            FakeImage('img1', log),
            FakeImage('img2', log,
                      teardown=InjectedFailure)]
        expected = [
            ('prepare', 'img1'),
            ('prepare', 'img2'),
            ('teardown', 'img1')]
        with pytest.raises(TeardownError):
            with prepared(images):
                pass
        assert expected == log

    def test_fail_inside_context(self):
        log = []
        images = [
            FakeImage('img', log)]
        expected = [
            ('prepare', 'img'),
            ('teardown', 'img')]
        with pytest.raises(InjectedFailure):
            with prepared(images):
                raise InjectedFailure()
        assert expected == log

    def test_fail_inside_context_with_teardown_failure(self):
        log = []
        images = [
            FakeImage('img', log,
                      teardown=InjectedFailure)]
        expected = [
            ('prepare', 'img')]
        with pytest.raises(RuntimeError):
            with prepared(images):
                raise RuntimeError()
        assert expected == log
