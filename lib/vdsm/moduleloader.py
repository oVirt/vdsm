# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
import importlib
import pkgutil


def load_modules(pkg_name):
    modules = {}
    for _, module_name, _ in pkgutil.iter_modules([pkg_name.__path__[0]]):
        full_name = '{}.{}'.format(pkg_name.__name__, module_name)
        modules[module_name] = importlib.import_module(full_name)
    return modules
