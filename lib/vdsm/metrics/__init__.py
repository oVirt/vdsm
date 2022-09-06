# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import importlib
from ..config import config

_reporter = None


def start():
    global _reporter
    if config.getboolean('metrics', 'enabled'):
        _reporter = importlib.import_module(
            'vdsm.metrics.' + config.get('metrics', 'collector_type')
        )
        _reporter.start(config.get('metrics', 'collector_address'))


def stop():
    global _reporter
    if _reporter:
        _reporter.stop()
        _reporter = None


def send(report):
    if _reporter:
        _reporter.send(report)
