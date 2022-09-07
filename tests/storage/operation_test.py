# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import time
import threading

from testlib import VdsmTestCase

from vdsm.common import cmdutils
from vdsm.common import concurrent
from vdsm.common import exception
from vdsm.storage import operation


class TestCommandRun(VdsmTestCase):

    def test_success(self):
        op = operation.Command(["true"])
        op.run()

    def test_failure(self):
        op = operation.Command(["false"])
        with self.assertRaises(cmdutils.Error):
            op.run()

    def test_error(self):
        op = operation.Command(
            ["sh", "-c", "echo -n out >&1; echo -n err >&2; exit 1"])
        with self.assertRaises(cmdutils.Error) as e:
            op.run()
        self.assertEqual(e.exception.rc, 1)
        self.assertEqual(e.exception.out, b"out")
        self.assertEqual(e.exception.err, b"err")

    def test_run_once(self):
        op = operation.Command(["true"])
        op.run()
        with self.assertRaises(RuntimeError):
            op.run()

    def test_output(self):
        op = operation.Command(["echo", "-n", "out"])
        out = op.run()
        self.assertEqual(out, b"out")

    def test_abort_created(self):
        op = operation.Command(["sleep", "5"])
        op.abort()
        with self.assertRaises(exception.ActionStopped):
            op.run()

    def test_abort_running(self):
        op = operation.Command(["sleep", "5"])
        aborted = threading.Event()

        def run():
            try:
                op.run()
            except exception.ActionStopped:
                aborted.set()

        t = concurrent.thread(run)
        t.start()
        try:
            # TODO: add way to wait until operation is stated?
            time.sleep(0.5)
            op.abort()
        finally:
            t.join()
        self.assertTrue(aborted.is_set())

    def test_abort_terminated(self):
        op = operation.Command(["true"])
        op.run()
        op.abort()


class TestCommandWatch(VdsmTestCase):

    def test_success(self):
        op = operation.Command(["true"])
        received = list(op.watch())
        self.assertEqual(received, [])

    def test_failure(self):
        op = operation.Command(["false"])
        with self.assertRaises(cmdutils.Error):
            list(op.watch())

    def test_error(self):
        op = operation.Command(
            ["sh", "-c", "echo -n out >&1; echo -n err >&2; exit 1"])
        out = bytearray()
        with self.assertRaises(cmdutils.Error) as e:
            for data in op.watch():
                out += data
        self.assertEqual(e.exception.rc, 1)
        self.assertEqual(e.exception.err, b"err")
        self.assertEqual(out, b"out")

    def test_run_once(self):
        op = operation.Command(["true"])
        op.run()
        with self.assertRaises(RuntimeError):
            list(op.watch())

    def test_output(self):
        op = operation.Command(["echo", "-n", "out"])
        out = bytearray()
        for data in op.watch():
            out += data
        self.assertEqual(out, b"out")

    def test_abort_created(self):
        op = operation.Command(["sleep", "5"])
        op.abort()
        with self.assertRaises(exception.ActionStopped):
            list(op.watch())

    def test_abort_running(self):
        op = operation.Command(["sleep", "5"])
        aborted = threading.Event()

        def run():
            try:
                list(op.watch())
            except exception.ActionStopped:
                aborted.set()

        t = concurrent.thread(run)
        t.start()
        try:
            # TODO: add way to wait until operation is stated?
            time.sleep(0.5)
            op.abort()
        finally:
            t.join()
        self.assertTrue(aborted.is_set())

    def test_abort_terminated(self):
        op = operation.Command(["true"])
        list(op.watch())
        op.abort()
