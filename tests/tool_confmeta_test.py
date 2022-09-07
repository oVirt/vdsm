# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from __future__ import print_function
import timeit

from testlib import VdsmTestCase
from testlib import temporaryPath

from vdsm.tool import confmeta


class TestConfmeta(VdsmTestCase):

    def test_owned_by_vdsm(self):
        data = (b"#REVISION: 1\n"
                b"#PRIVATE: NO\n")
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, 1)
            self.assertEqual(md.private, False)

    def test_owned_by_vdsm_by_default(self):
        data = b"#REVISION: 1\n"
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, 1)
            self.assertEqual(md.private, False)

    def test_owned_by_sysadmin(self):
        data = (b"#REVISION: 1\n"
                b"#PRIVATE: YES\n")
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, 1)
            self.assertEqual(md.private, True)

    def test_no_revision(self):
        data = b"#PRIVATE: NO\n"
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, None)
            self.assertEqual(md.private, False)

    def test_no_revision_owned_by_sysadmin(self):
        data = b"#PRIVATE: YES\n"
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, None)
            self.assertEqual(md.private, True)

    def test_empty(self):
        data = b""
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, None)
            self.assertEqual(md.private, False)

    def test_no_metadata(self):
        data = (b"# There is no metadata here\n"
                b"Actual file data...\n")
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, None)
            self.assertEqual(md.private, False)

    def test_order_does_not_matter(self):
        data = (b"# A comment\n"
                b"#PRIVATE: NO\n"
                b"# Another comment\n"
                b"#REVISION: 1\n"
                b"# Last comment\n"
                b"Actual file data...")
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, 1)
            self.assertEqual(md.private, False)

    def test_ignore_file_body(self):
        data = (b"#REVISION: 1\n"
                b"#PRIVATE: YES\n"
                b"REVISION: 2\n"
                b"PRIVATE: NO\n")
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, 1)
            self.assertEqual(md.private, True)

    def test_must_start_with_metadata(self):
        data = (b"There is no metadata here\n"
                b"#REVISION: 1\n"
                b"#PRIVATE: YES\n")
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, None)
            self.assertEqual(md.private, False)

    def test_ignore_unknonwn_tags(self):
        data = (b"#UNKNOWN: VALUE\n"
                b"#REVISION: 1\n"
                b"#PRIVATE: YES\n")
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, 1)
            self.assertEqual(md.private, True)

    def test_last_value_win(self):
        data = (b"#REVISION: 4\n"
                b"#REVISION: 3\n"
                b"#PRIVATE: YES\n")
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, 3)
            self.assertEqual(md.private, True)

    def test_no_whitespace(self):
        data = (b"#REVISION:1\n"
                b"#PRIVATE:NO\n")
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, 1)
            self.assertEqual(md.private, False)

    def test_extra_whitespace(self):
        data = (b"#REVISION:  1   \n"
                b"#PRIVATE:   NO  \n")
        with temporaryPath(data=data) as path:
            md = confmeta.read_metadata(path)
            self.assertEqual(md.revision, 1)
            self.assertEqual(md.private, False)

    def test_invalid_revision(self):
        data = (b"#REVISION: invalid\n")
        with temporaryPath(data=data) as path:
            with self.assertRaises(ValueError):
                confmeta.read_metadata(path)

    def test_invalid_private(self):
        data = (b"#REVISION: 1\n"
                b"#PRIVATE:\n")
        with temporaryPath(data=data) as path:
            with self.assertRaises(ValueError):
                confmeta.read_metadata(path)

    def test_benchmark(self):
        setup = """
from vdsm.tool import confmeta
path = "%s"

def bench():
    confmeta.read_metadata(path)
"""
        data = b"""\
# This file is managed by vdsm
# Options:
#   ...
#   ...
#   ...
#REVISION: 1
#PRIVATE: NO

file body...
"""
        count = 100
        with temporaryPath(data=data) as path:
            elapsed = timeit.timeit("bench()",
                                    setup=setup % path,
                                    number=count)

        print("%.6f seconds (%.6f per op)" % (elapsed, elapsed / count))
