#
# Copyright 2015-2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2 of the License, or
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
"""
Export a function to raise a real libvirt.libvirtError, so the client code
could keep catching libvirtErrors as usual.
"""

from __future__ import absolute_import
from __future__ import division


import libvirt


# TODO: VIR_FROM_PROXY is mostly guesswork,
# need to check if it's really correct.
def throw(code=libvirt.VIR_ERR_OPERATION_UNSUPPORTED,
          message='operation not supported',
          domain=libvirt.VIR_FROM_PROXY,
          warning=False):
    level = libvirt.VIR_ERR_WARNING if warning else libvirt.VIR_ERR_ERROR
    e = libvirt.libvirtError(defmsg='')
    # we have to override the value to get what we want
    # err might be None
    e.err = (code,          # error code
             domain,        # error domain
             message,       # error message
             level,         # error level
             '', '', '',    # str1, str2, str3,
             -1, -1)        # int1, int2
    raise e
