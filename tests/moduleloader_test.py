# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager
import importlib
import os
import sys
from vdsm import moduleloader
from vdsm.common import fileutils
from testlib import permutations, expandPermutations, namedTemporaryDir
from testlib import forked
from testlib import VdsmTestCase as TestCaseBase


@expandPermutations
class ImportModulesTest(TestCaseBase):

    @contextmanager
    def _setup_test_modules(self, files):
        with namedTemporaryDir() as path:
            for f in files:
                fileutils.touch_file(os.path.join(path, f))
            fileutils.touch_file(os.path.join(path, '__init__.py'))
            sys.path.insert(0, os.path.dirname(path))
            yield importlib.import_module(os.path.basename(path))

    @permutations([
        (('a.py', 'b.py'), ('a', 'b')),
        (('a.py', 'b.py', 'a.pyioas'), ('a', 'b')),
        (('a.py', 'b.py', '_my.py'), ('a', 'b', '_my')),
    ])
    @forked
    def test_import_modules(self, files, expected_modules):
        with self._setup_test_modules(files) as module_name:
            result = moduleloader.load_modules(module_name)

        result = frozenset(result.keys())
        expected = frozenset(expected_modules)
        self.assertEqual(result, expected)
