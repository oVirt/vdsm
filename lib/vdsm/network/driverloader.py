# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

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
