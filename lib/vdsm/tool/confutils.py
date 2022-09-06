# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import errno
import os

from vdsm.tool.configfile import ConfigFile

'''
The following function are being used for property conf file configuration
For example, in libvirt and abrt configurators we use those helper functions
to manage the files in the following way:

FILES = {
    [file_name]: {
        path: [path_to_file],
        configure: [function that configures this file],
        removeConf: [function that removes vdsm config from the file],
        persisted: [True or False if we want to save changes],
        fragments: [dict content of the configurations],
    }
}

'''


def get_file_path(fname, files):
    """
    Helper func to get 'path' key for specific file key.
    """
    return files[fname]['path']


def remove_conf(files, version):
    """
    calling removeConf func for all files in dict with conf version to remove
    """
    for cfile, content in files.items():
        content['removeConf'](content['path'], version)


def add_section(content, version, vdsmConfiguration={}):
    """
    Add a 'configuration section by vdsm' part to a config file.
    This section contains only keys not originally defined
    The section headers will include the current configuration version.
    """
    configuration = {}
    for fragment in content['fragments']:
        if vdsmConfiguration:
            if is_applicable(fragment, vdsmConfiguration):
                configuration.update(fragment['content'])
        else:
            configuration.update(fragment['content'])
    if configuration:
        with open_config(content['path'], version) as conff:
            for key, val in configuration.items():
                conff.addEntry(key, val)


def remove_section(path, version):
    """
    remove entire 'configuration section by vdsm' section.
    section is removed regardless of it's version.
    """
    if os.path.exists(path):
        with open_config(path, version) as conff:
            conff.removeConf()


def remove_file(content, version, vdsmConfiguration):
    """
    Helper configure func that removes a file if exists.
    This being used once - TODO: consider if to leave it in libvirt.py
    """
    try:
        os.unlink(content['path'])
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise


def get_persisted_files(files):
    """
    get files where vdsm is expected to add a section.
    """
    return [
        cfile['path'] for cfile in files.values()
        if cfile['persisted']
    ]


def open_config(path, conf_version):
    return ConfigFile(path, conf_version)


def is_applicable(fragment, vdsmConfiguration):
    """
    Return true if 'fragment' should be included for current
    configuration. An applicable fragment is a fragment who's list
    of conditions are met according to vdsmConfiguration.
    """
    applyFragment = True
    for key, booleanValue in fragment['conditions'].items():
        if vdsmConfiguration[key] != booleanValue:
            applyFragment = False
    return applyFragment
