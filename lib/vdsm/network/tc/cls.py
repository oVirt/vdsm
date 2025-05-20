# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from __future__ import division

from copy import deepcopy

from . import _parser
from . import _wrapper

_TC_PRIO_MAX = 15


def add(dev, kind, parent, classid, **opts):
    """Adds a class to a device. Opts should be used with list values for
    complex inputs, e.g. {'ls': ['rate', '400kbps']}"""
    command = [
        'class',
        'add',
        'dev',
        dev,
        'parent',
        parent,
        'classid',
        classid,
    ]
    command.append(kind)
    adapted_ops = _adapt_qos_options_link_share(opts)
    for key, value in _qos_to_str_dict(adapted_ops).items():
        if isinstance(value, str):
            command += [key, value]
        else:
            command.append(key)
            command += value
    _wrapper.process_request(command)


def delete(dev, classid, parent=None):
    command = ['class', 'del', 'dev', dev, 'classid', classid]
    if parent is not None:
        command += ['parent', parent]
    _wrapper.process_request(command)


def show(dev, parent=None, classid=None):
    command = ['class', 'show', 'dev', dev]
    if parent is not None:
        command += ['parent', parent]
    if classid is not None:
        command += ['classid', classid]
    return _wrapper.process_request(command)


def parse(tokens):
    """Takes a token generator and returns a dictionary of general class
    attributes and class kind (TCA_KIND) specific attributes."""
    kind = next(tokens)
    data = {'kind': kind, 'handle': _parser.parse_str(tokens)}
    for token in tokens:
        if token in ('dev', 'leaf', 'parent'):
            data[token] = _parser.parse_str(tokens)
        elif token == 'root':
            data[token] = _parser.parse_true(tokens)
        else:
            # Finished with the general class attrs. Loop for kind attrs
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
    if kind == 'hfsc' and 'sc' in data.get(kind, {}):
        #  sc is a shorthand for when rt an ls are equal. For reporting to the
        #  api we separate it into the two components
        curve = data[kind].pop('sc')
        data[kind]['rt'] = data[kind]['ls'] = curve

    return _adapt_qos_options_link_share_for_reporting(data)


def _parse_hfsc_curve(tokens):
    return dict(
        (
            (
                _parser.parse_str(tokens),  # Get and consume 'm1'
                _parser.parse_rate(tokens),
            ),
            (
                _parser.parse_str(tokens),  # Get and consume 'd'
                _parser.parse_time(tokens),
            ),
            (
                _parser.parse_str(tokens),  # Get and consume 'm2'
                _parser.parse_rate(tokens),
            ),
        )
    )


def _qos_to_str_dict(qos):
    data = {}
    for curve, attrs in qos.items():
        data[curve] = []
        if 'm1' in attrs:
            data[curve] += [
                'm1',
                '%sbit' % attrs.get('m1', 0),
                'd',
                '%sus' % attrs.get('d', 0),
            ]
        if 'm2' in attrs:
            data[curve] += ['m2', '%sbit' % attrs.get('m2', 0)]
    return data


def _adapt_qos_options_link_share_for_reporting(qos_opts):
    """See _adapt_qos_options_link_share"""
    adapted_qos_ops = deepcopy(qos_opts)
    hfsc = adapted_qos_ops.get('hfsc', {})
    link_share = hfsc.get('ls', {})
    for k in link_share.keys():
        link_share[k] //= 8
    return adapted_qos_ops


def _adapt_qos_options_link_share(qos_opts):
    """This function adapts the relative parameters used for tc outbound QOS.
    They are multiplied by 8 because tc always rounds them down to multiples of
    8, thus eliminating rounding errors."""
    adapted_qos_ops = deepcopy(qos_opts)
    link_share = adapted_qos_ops.get('ls', {})
    for k in link_share.keys():
        link_share[k] *= 8
    return adapted_qos_ops


_spec = {
    'hfsc': {
        'sc': _parse_hfsc_curve,
        'rt': _parse_hfsc_curve,
        'ls': _parse_hfsc_curve,
        'ul': _parse_hfsc_curve,
    }
}
