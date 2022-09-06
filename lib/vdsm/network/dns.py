# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import logging

DNS_CONF_FILE = '/etc/resolv.conf'


def get_host_nameservers():
    """Returns a list of nameservers listed in /etc/resolv.conf"""
    try:
        with open(DNS_CONF_FILE, 'r') as file_object:
            file_text = file_object.read()
    except IOError as e:
        logging.warning('Failed to read %s: %s', DNS_CONF_FILE, e)
        return []
    return _parse_nameservers(file_text)


def _parse_nameservers(file_text):
    nameservers = []
    for line in file_text.splitlines():
        words = line.strip().split()
        if len(words) < 2:
            continue
        if words[0] == 'nameserver':
            nameservers.append(words[1])
    return nameservers
