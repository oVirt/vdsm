#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import io
import pickle
import sys
import yaml


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
                        protocol=4)


def main():
    schema_path = sys.argv[1]
    pickled_schema_path = sys.argv[2]

    _dump_pickled_schema(schema_path, pickled_schema_path)


if __name__ == '__main__':
    main()
