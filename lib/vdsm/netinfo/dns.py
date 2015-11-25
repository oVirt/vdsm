#
# Copyright 2015 Hat, Inc.
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
from __future__ import absolute_import

DNS_CONF_FILE = '/etc/resolv.conf'


def get_host_nameservers():
    """Returns a list of nameservers listed in /etc/resolv.conf"""
    with open(DNS_CONF_FILE, 'r') as file_object:
        file_text = file_object.read()
    return _parse_dnss(file_text)


def _parse_dnss(file_text):
    dnss = []
    for line in file_text.splitlines():
        words = line.strip().split()
        if words[0] == 'nameserver':
            dnss.append(words[1])
    return dnss
