#
# Copyright 2015 Red Hat, Inc.
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


_RPFILTER_STRICT = '1'
_RPFILTER_LOOSE = '2'


def set_rp_filter(dev, mode):
    path = '/proc/sys/net/ipv4/conf/%s/rp_filter' % dev
    with open(path, 'w') as rp_filter:
        rp_filter.write(mode)


def set_rp_filter_loose(dev):
    set_rp_filter(dev, _RPFILTER_LOOSE)


def set_rp_filter_strict(dev):
    set_rp_filter(dev, _RPFILTER_STRICT)
