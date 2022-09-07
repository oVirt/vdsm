# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import pytest

from vdsm.rpc.http import HttpDetector
from yajsonrpc import stomp


@pytest.mark.parametrize("data", [
    b"PUT /",
    b"GET /",
])
def test_http_detector_should_detect_http_protocol(data):
    assert HttpDetector(server=None).detect(data)


@pytest.mark.parametrize("data", [
    b"smth",
    b"\x23\x54"
])
def test_http_detector_should_reject_non_http_protocol(data):
    assert not HttpDetector(server=None).detect(data)


@pytest.mark.parametrize("data", [c.encode("utf-8") for c in stomp.COMMANDS])
def test_http_detector_should_reject_stomp_commands(data):
    assert not HttpDetector(server=None).detect(data)
