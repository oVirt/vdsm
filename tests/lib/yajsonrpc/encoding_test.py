#
# Copyright 2015-2019 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import pytest

from yajsonrpc.stomp import decode_value, encode_value


@pytest.mark.parametrize("value, expected", [
    (u'abc', b'abc'),
    ('abc', b'abc'),
    pytest.param(u'\u0105b\u0107', b'\xc4\x85b\xc4\x87',
                 id='utf-8 string with localized characters'),
])
def test_encode_should_handle_strings(value, expected):
    assert encode_value(value) == expected


# TODO: needs to be removed - decoding doesn't do the opposite
def test_encode_should_handle_ints():
    assert encode_value(5) == b'5'


# TODO: to be removed when we go py3-only
def test_encode_should_accept_bytes():
    assert encode_value(b'abc') == b'abc'


# https://stomp.github.io/stomp-specification-1.2.html#Value_Encoding
@pytest.mark.parametrize('value, expected', [
    (b'\r', br'\r'),  # \r (octet 92 and 114) translates
                      # to carriage return (octet 13)
    (b'\n', br'\n'),  # \n (octet 92 and 110) translates
                      # to line feed (octet 10)
    (b':', br'\c'),   # \c (octet 92 and 99) translates to : (octet 58)
    (b'\\', br'\\'),  # \\ (octet 92 and 92) translates to \ (octet 92)
    (b'\r\r\n:\\\n', br'\r\r\n\c\\\n'),
    (u'\r', br'\r'),  # same patterns as unicode literals
    (u'\n', br'\n'),
    (u':', br'\c'),
    (u'\\', br'\\'),
    (u'\r\r\n:\\\n', br'\r\r\n\c\\\n')
])
def test_encode_should_escape_characters(value, expected):
    assert encode_value(value) == expected


def test_encode_should_raise_for_unsupported_types():
    with pytest.raises(ValueError) as err:
        encode_value(5.4)

    assert 'Unable to encode' in str(err.value)
    assert repr(5.4) in str(err.value)


# https://stomp.github.io/stomp-specification-1.2.html#Value_Encoding
def test_decode_should_raise_for_sequences_with_colon():
    with pytest.raises(ValueError) as err:
        decode_value(b'abc:def')

    assert 'abc:def' in str(err.value)
    assert 'contains illegal character' in str(err.value)


# https://stomp.github.io/stomp-specification-1.2.html#Value_Encoding
@pytest.mark.parametrize('value, expected', [
    (br'\r', u'\r'),  # \r (octet 92 and 114) translates
                      # to carriage return (octet 13)
    (br'\n', u'\n'),  # \n (octet 92 and 110) translates
                      # to line feed (octet 10)
    (br'\c', u':'),   # \c (octet 92 and 99) translates to : (octet 58)
    (br'\\', u'\\'),  # \\ (octet 92 and 92) translates to \ (octet 92)
    (br'\\\r\r\n\r\\', u'\\\r\r\n\r\\')
])
def test_decode_should_unescape_characters(value, expected):
    assert decode_value(value) == expected


# https://stomp.github.io/stomp-specification-1.2.html#Value_Encoding
def test_decode_should_raise_for_invalid_escape_sequences():
    with pytest.raises(ValueError) as err:
        decode_value(b'Evil sequence \\m')

    assert 'Evil sequence' in str(err.value)
    assert 'contains invalid escape sequence' in str(err.value)
    assert '\\m' in str(err.value)


@pytest.mark.parametrize('value, expected', [
    (b'abc', u'abc'),
    pytest.param(b'\xc4\x85b\xc4\x87', u'\u0105b\u0107',
                 id='utf-8 string with localized characters'),
])
def test_decode_should_handle_bytes(value, expected):
    assert decode_value(value) == expected


def test_decode_should_raise_for_unsupported_types():
    with pytest.raises(ValueError) as err:
        decode_value(u'abc')

    assert 'Unable to decode non-binary value' in str(err.value)
    assert repr(u'abc') in str(err.value)


@pytest.mark.parametrize('value', [
    u'accept-version',
    u'1.2:',
    u'98c592f4-e2e2-46ea-b7b6-aa4f57f924b9\n\r',
    u'98c592f4\\-e2e2-46ea-b7b6-aa4f57f924b9',
])
def test_encoding_process_should_be_reversible(value):
    assert decode_value(encode_value(value)) == value
