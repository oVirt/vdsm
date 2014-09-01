#
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


from . import vmxml


class Base(vmxml.Device):
    __slots__ = ('deviceType', 'device', 'alias', 'specParams', 'deviceId',
                 'conf', 'log', '_deviceXML', 'type', 'custom')

    def __init__(self, conf, log, **kwargs):
        self.conf = conf
        self.log = log
        self.specParams = {}
        self.custom = kwargs.pop('custom', {})
        for attr, value in kwargs.iteritems():
            try:
                setattr(self, attr, value)
            except AttributeError:  # skip read-only properties
                self.log.debug('Ignoring param (%s, %s) in %s', attr, value,
                               self.__class__.__name__)
        self._deviceXML = None

    def __str__(self):
        attrs = [':'.join((a, str(getattr(self, a, None)))) for a in dir(self)
                 if not a.startswith('__')]
        return ' '.join(attrs)
