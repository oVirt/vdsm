# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from functools import partial

from . import _parser
from . import _wrapper

_TC_PRIO_MAX = 15


def add(dev, kind, parent=None, handle=None, **opts):
    command = ['qdisc', 'add', 'dev', dev]
    if kind != 'ingress':
        if parent is None:
            command.append('root')
        else:
            command += ['parent', parent]
    if handle is not None:
        command += ['handle', handle]
    command.append(kind)
    for key, value in opts.items():
        command += [key, value]
    _wrapper.process_request(command)


def delete(dev, kind=None, parent=None, handle=None, **opts):
    command = ['qdisc', 'del', 'dev', dev]
    if kind != 'ingress':
        if parent is None:
            command.append('root')
        else:
            command += ['parent', parent]
    if handle is not None:
        command += ['handle', handle]
    if kind is not None:
        command.append(kind)
    for key, value in opts.items():
        command += [key, value]
    _wrapper.process_request(command)


def replace(dev, kind, parent=None, handle=None, **opts):
    command = ['qdisc', 'replace', 'dev', dev]
    if kind != 'ingress':
        if parent is None:
            command.append('root')
        else:
            command += ['parent', parent]
    if handle is not None:
        command += ['handle', handle]
    command.append(kind)
    for key, value in opts.items():
        command += [key, value]
    _wrapper.process_request(command)


def show(dev=None):
    command = ['qdisc', 'show']
    if dev:
        command += ['dev', dev]
    return _wrapper.process_request(command)


def parse(tokens):
    """Takes a token generator and returns a dictionary of general qdisc
    attributes and kind (kernel's TCA_KIND) specific attributes"""
    kind = next(tokens)
    data = {'kind': kind, 'handle': next(tokens)}
    for token in tokens:
        if token == 'root':
            data[token] = _parser.parse_true(tokens)
        elif token in ('dev', 'parent'):
            data[token] = _parser.parse_str(tokens)
        elif token == 'refcnt':
            data[token] = _parser.parse_int(tokens)
        else:
            # Finished with general qdisc attrs. Loop for kind attrs
            spec_parser = _spec.get(kind, ())
            while True:
                if token in spec_parser:
                    value = spec_parser[token](tokens)
                    try:
                        data[kind][token] = value
                    except KeyError:
                        data[kind] = {token: value}
                else:
                    pass  # Consume anything that we don't know how to parse
                try:
                    token = next(tokens)
                except StopIteration:
                    break

    return data


def _parse_limit(tokens):
    return int(next(tokens)[:-1])  # leave off the trailing 'p'


def _parse_pfifo_fast_priomap(tokens):
    return [int(next(tokens)) for _ in range(_TC_PRIO_MAX)]


_spec = {
    'fq_codel': {
        'ecn': _parser.parse_true,
        'flows': _parser.parse_int,
        'interval': _parser.parse_time,
        'limit': _parse_limit,
        'quantum': _parser.parse_int,
        'target': _parser.parse_time,
    },
    'hfsc': {'default': partial(_parser.parse_int, base=16)},
    'ingress': {},
    'pfifo_fast': {
        'bands': _parser.parse_int,
        'multiqueue': _parser.parse_str,
        'priomap': _parse_pfifo_fast_priomap,
    },
}
