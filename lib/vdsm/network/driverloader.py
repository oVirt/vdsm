# Copyright 2017 Red Hat, Inc.
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

from importlib import import_module
from pkgutil import iter_modules

from vdsm.common.cache import memoized


class NoDriverError(Exception):
    pass


@memoized
def load_drivers(driver_class, package_name, package_path):
    drivers = {}
    for _, module_name, _ in iter_modules([package_path]):
        module = import_module('{}.{}'.format(package_name, module_name))
        if hasattr(module, driver_class):
            drivers[module_name] = getattr(module, driver_class)
    return drivers


def get_driver(driver, drivers):
    try:
        return drivers[driver]
    except KeyError:
        raise NoDriverError(driver)
