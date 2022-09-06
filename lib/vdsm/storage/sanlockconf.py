# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Read and write sanlock configuration file.

This is a minimal parser, reading sanlock configuration as a dict of strings,
and writing the dict back to the file.

This is a very minimal implementation:
- Comments in the original configuration are not preserved.
- There is no validation for option names or values
- All options are treated as strings.

Example usage:

    >>> conf = sanlockconf.load()
    >>> conf
    {'max_worker_threads': '50'}
    >>> conf['our_host_name'] = 'c59d39ca-620b-4aad-8b50-97833e366664'
    >>> sunlockconf.dump(conf)

For details on the file syntax and available options see:
https://pagure.io/sanlock/blob/master/f/src/main.c#_2714
"""

import io

from . import fileUtils

SANLOCK_CONF = "/etc/sanlock/sanlock.conf"


def load():
    """
    Read sanlock configuration to dict of option: value strings.
    """
    try:
        with open(SANLOCK_CONF) as f:
            conf = {}
            for line in f:
                if line.startswith(("#", "\n", " ", "\t")):
                    continue
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                if not key:
                    continue
                conf[key.rstrip()] = val.strip()
            return conf
    except FileNotFoundError:
        return {}


def dump(conf):
    """
    Backup current configuration and write new configuration.

    Arguments:
        conf (dict): Dict of option: value strings

    Returns:
        Path to backup file if the original configuration was backed up.
    """
    backup_path = fileUtils.backup_file(SANLOCK_CONF)

    buf = io.StringIO()
    buf.write("# Configuration for vdsm\n")
    for key, val in conf.items():
        buf.write("{} = {}\n".format(key, val))

    data = buf.getvalue().encode("utf-8")
    fileUtils.atomic_write(SANLOCK_CONF, data, relabel=True)

    return backup_path
