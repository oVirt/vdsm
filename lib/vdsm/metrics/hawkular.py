# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import collections
import logging
import threading

import six

from vdsm.common import concurrent
from vdsm.config import config

try:
    from hawkular import metrics
except ImportError as e:
    raise ModuleNotFoundError(str(e))

_running = False
_queue = collections.deque(maxlen=config.getint('metrics', 'queue_size'))
_cond = threading.Condition(threading.Lock())
_STOP = object()


def start(address):
    global _running
    if _running:
        raise RuntimeError('trying to start reporter while running')
    logging.info("Starting hawkular reporter")
    concurrent.thread(_run, name='hawkular', args=(address,)).start()
    _running = True


def stop():
    logging.info("Stopping hawkular reporter")
    with _cond:
        _queue.clear()
        _queue.append(_STOP)
        _cond.notify()


def send(report):
    metrics_list = [_get_gauge_metric(name, value)
                    for name, value in six.iteritems(report)]
    _queue.append(metrics_list)
    with _cond:
        _cond.notify()


def _get_gauge_metric(name, value):
    return metrics.create_metric(metrics.MetricType.Gauge, name,
                                 metrics.create_datapoint(float(value)))


def _run(address):
    global _running
    client = metrics.HawkularMetricsClient(tenant_id="oVirt",
                                           host=address)
    while True:
        with _cond:
            while not _queue:
                _cond.wait()
        while _queue:
            items = _queue.popleft()
            if items is _STOP:
                break
            client.put(items)
    _running = False
