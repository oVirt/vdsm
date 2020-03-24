#!/usr/bin/python3
#
# Copyright 2018 Red Hat, Inc.
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

import io
import sys
import yaml

from vdsm.common.compat import pickle


def _load_yaml_file(file_path):
    if hasattr(yaml, 'CSafeLoader'):
        loader = yaml.CLoader
    else:
        loader = yaml.SafeLoader
    yaml_file = yaml.load(file_path, Loader=loader)
    return yaml_file


def _dump_pickled_schema(schema_path, pickled_schema_path):
    with io.open(schema_path, 'rb') as f:
        loaded_schema = _load_yaml_file(f)
        with io.open(pickled_schema_path, 'wb') as pickled_schema:
            pickle.dump(loaded_schema,
                        pickled_schema,
                        protocol=pickle.HIGHEST_PROTOCOL)


def main():
    schema_path = sys.argv[1]
    pickled_schema_path = sys.argv[2]

    _dump_pickled_schema(schema_path, pickled_schema_path)


if __name__ == '__main__':
    main()
