# Copyright 2014 Red Hat, Inc.
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
import logging

from . import _parser
from . import _wrapper


def delete(dev, pref, parent=None, protocol=None):
    command = ['filter', 'del', 'dev', dev, 'pref', str(pref)]
    if parent is not None:
        command += ['parent', parent]
    if protocol is not None:
        command += ['protocol', protocol]
    _wrapper.process_request(command)


def replace(
    dev,
    root=False,
    parent=None,
    handle=None,
    pref=None,
    protocol=None,
    estimator=None,
    actions=(),
    **opts
):
    """Replaces a filter. actions should be an iterable of lists of action
    definition tokens. opts should be used for the matches (such as u32)"""
    command = ['filter', 'replace', 'dev', dev]
    if protocol is not None:
        command += ['protocol', protocol]
    if root:
        command += ['root']
    elif parent is not None:
        command += ['parent', parent]
    if handle is not None:
        command += ['handle', handle]
    if pref is not None:
        command += ['pref', str(pref)]
    if estimator is not None:
        command += ['estimator', estimator]
    for key, value in opts.items():
        if isinstance(value, str):
            command += [key, value]
        else:
            command.append(key)
            command += value
    for action in actions:
        command += action
    _wrapper.process_request(command)


def show(dev, parent=None, pref=None):
    command = ['filter', 'show', 'dev', dev]
    if parent is not None:
        command += ['parent', parent]
    if pref is not None:
        command += ['pref', str(pref)]
    return _wrapper.process_request(command)


def parse(tokens):
    """Parses a filter entry token generator into a data dictionary"""
    data = {}
    for token in tokens:
        if token == 'root':
            data['root'] = _parser.parse_true(tokens)
        elif token == 'pref':
            data[token] = _parser.parse_int(tokens)
        elif token in ('dev', 'parent', 'protocol'):
            data[token] = _parser.parse_str(tokens)
        elif token in _CLASSES:
            data['kind'] = token
            break
    # At this point there should be a filter kind
    _filter_cls_parser = _CLASSES.get(data['kind'])
    if _filter_cls_parser is not None:
        data[data['kind']] = _filter_cls_parser(tokens)
    return data


def _parse_u32(tokens):
    """Returns a dictionary with the parsed information and consumes the parsed
    elements from the input list"""
    data = {}
    for token in tokens:
        if token in ('fh', 'link'):
            data[token] = _parser.parse_str(tokens)
        elif token == 'chain':
            data['chain'] = _parser.parse_int(tokens)
        elif token == 'order':
            data[token] = _parser.parse_int(tokens)
        elif token in ('*flowid', 'flowid'):
            data['flowid'] = _parser.parse_str(tokens)
        elif token == 'terminal':
            data['terminal'] = _parser.parse_true(tokens)
            _parser.consume(tokens, 'flowid')
            _parser.consume(tokens, '???', 'not_in_hw')
        elif token == 'ht':
            _parser.consume(tokens, 'divisor')
            data['ht_divisor'] = _parser.parse_int(tokens)
        elif token == 'key':
            _parser.consume(tokens, 'ht')
            data['key_ht'] = _parser.parse_int(tokens, 16)
            _parser.consume(tokens, 'bkt')
            data['key_bkt'] = _parser.parse_int(tokens, 16)
        elif token == '???':
            continue
        elif token == 'not_in_hw':
            continue
        elif token == _parser.LINE_DELIMITER:  # line break
            continue
        elif token == 'match':
            match_first = _parser.parse_str(tokens)
            if match_first.lower() == 'ip':
                # IP matching is not yet implemented.
                _parser.parse_skip_line(tokens)
                data['match'] = None
            else:
                data['match'] = _parse_match_raw(match_first, tokens)
        elif token == 'action':
            try:
                data['actions'].append(_parse_action(tokens))
            except KeyError:
                data['actions'] = [_parse_action(tokens)]
        else:
            break  # We should not get here unless iproute adds fields. Log?
    return data


def _parse_ematch(tokens):
    """Parses tokens describing a raw ematch, (see man tc-ematch) e.g.,
    'meta(vlan mask 0x00000000 eq 16)' into a data dictionary.
    currently only a single 'meta' module predicate is supported"""
    data = {}
    for token in tokens:
        if token == _parser.LINE_DELIMITER:  # line break
            continue
        elif token == 'handle':
            data[token] = _parser.parse_str(tokens)
        elif token == 'flowid':
            data['flowid'] = _parser.parse_str(tokens)
        elif '(' in token:
            module, first_arg = token.split('(', 1)
            if module != 'meta':
                _parser.parse_skip_line(tokens)
            data['module'] = module
            data.update(_parse_ematch_match(first_arg, tokens))
        else:
            logging.info('could not parse ematch filter. token=%s', token)
    return data


def _parse_match_raw(val_mask, tokens):
    """Parses tokens describing a raw match, e.g.,
    'match 001e0000/0fff0000 at -4' into a data dictionary"""
    value, mask = val_mask.split('/')
    value = int(value, 16)
    mask = int(mask, 16)
    _parser.consume(tokens, 'at')
    offset = _parser.parse_int(tokens)
    return {'value': value, 'mask': mask, 'offset': offset}


def _parse_ematch_match(first_arg, tokens):
    if first_arg != 'vlan':
        _parser.parse_skip_line(tokens)
        # empty data. currently we do not support other ematches than vlan
        return {}

    data = {'object': first_arg}
    for token in tokens:
        if token == _parser.LINE_DELIMITER:  # line break
            return data
        elif token in ('eq', 'lt', 'gt'):
            data['relation'] = token
            data['value'] = int(next(tokens).strip(')'))
        elif token == 'mask':
            data['mask'] = _parser.parse_hex(tokens)
        else:
            logging.debug('unsupported token for vlan: %s', token)
    return data


def _parse_action(tokens):
    """Returns a dictionary with the parsed information and consumes the parsed
    elements from the input list"""
    data = {}
    for token in tokens:
        if token == 0:
            continue
        if token == 'order':
            data[token] = _parse_action_order(tokens)
            data['kind'] = _parser.parse_str(tokens)
            action_opt_parse = _ACTIONS.get(data['kind'])
            if action_opt_parse is not None:
                data.update(action_opt_parse(tokens))
            return data
    raise _parser.TCParseError('Unexpected filter action format')


def _parse_mirred(tokens):
    """Parses the tokens of a mirred action into a data dictionary"""
    data = {}
    # Get the first token without the opening paren
    action = _parser.parse_str(tokens)[1:]
    if action == 'unkown':
        data['action'] = action
    else:
        data['action'] = '%s_%s' % (
            action.lower(),
            _parser.parse_str(tokens).lower(),
        )
    _parser.consume(tokens, 'to')
    _parser.consume(tokens, 'device')
    data['target'] = _parser.parse_str(tokens)[:-1]
    data['op'] = _parser.parse_str(tokens)
    _parser.consume(tokens, _parser.LINE_DELIMITER)
    for token in tokens:
        if token in ('index', 'ref', 'bind'):
            data[token] = _parser.parse_int(tokens)
        elif token == 0:
            break
        else:
            # We should not get here unless iproute adds fields. In any case,
            # we only need to report the fields that we care about. Safe to
            # stop parsing
            break
    return data


def _parse_action_order(tokens):
    """Return the int part or an action order, removing the training ':'"""
    return int(next(tokens)[:-1])


_ACTIONS = {
    'csum': None,
    'gact': None,
    'ipt': None,
    'mirred': _parse_mirred,
    'nat': None,
    'pedit': None,
    'police': None,
    'simple': None,
    'skbedit': None,
    'xt': None,
}


_CLASSES = {
    'basic': _parse_ematch,
    'cgroup': None,
    'flow': None,
    'fw': None,
    'route': None,
    'rsvp': None,
    'tcindex': None,
    'u32': _parse_u32,
}
