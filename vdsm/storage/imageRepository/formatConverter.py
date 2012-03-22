#
# Copyright 2012 Red Hat, Inc.
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

from functools import partial


def legacyConverter(targetVersion, domain):
    domain.upgrade(int(targetVersion))
    return domain

v2LegacyConverter = partial(legacyConverter, 2)

_IMAGE_REPOSITORY_CONVERSION_TABLE = {
        ('0', '2'): v2LegacyConverter
        }


class FormatConverter(object):
    def __init__(self, conversionTable):
        self._convTable = conversionTable

    def _getConverter(self, sourceFormat, targetFormat):
        return self._convTable[(sourceFormat, targetFormat)]

    def convert(self, imageRepo, targetFormat):
        sourceFormat = imageRepo.getFormat()
        if sourceFormat == targetFormat:
            return

        converter = self._getConverter(sourceFormat, targetFormat)
        converter(imageRepo)


def DefaultFormatConverter():
    return FormatConverter(_IMAGE_REPOSITORY_CONVERSION_TABLE)
