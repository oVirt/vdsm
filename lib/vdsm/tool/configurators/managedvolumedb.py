#
# Copyright 2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import os
import sys

from vdsm import constants
from vdsm.common import errors
from vdsm.storage import fileUtils
from vdsm.storage import managedvolumedb as mvdb

from . import YES, NO


class IncorrectDBVersion(errors.Base):

    msg = "Version of managed volumes database is not correct"


def configure():
    """
    Create database for managed volumes
    """
    if not _db_exists():
        sys.stdout.write("Creating managed volumes database at %s\n" %
                         mvdb.DB_FILE)
        mvdb.create_db()
        _set_db_ownership()
    else:
        if not _db_owned_by_vdsm():
            _set_db_ownership()
        if not _db_version_correct():
            raise IncorrectDBVersion


def isconfigured():
    """
    Return YES if managedvolumedb is configured, otherwise NO
    """
    if _db_exists() and _db_owned_by_vdsm() and _db_version_correct():
        sys.stdout.write("Managed volume database is already configured\n")
        return YES
    else:
        sys.stdout.write("Managed volume database requires configuration\n")
        return NO


def removeConf():
    """
    Remove database file
    """
    if os.path.isfile(mvdb.DB_FILE):
        sys.stdout.write("Removing database file %s\n" % mvdb.DB_FILE)
        os.remove(mvdb.DB_FILE)


def _set_db_ownership():
    sys.stdout.write("Setting up ownership of database file to %s:%s\n" %
                     (constants.VDSM_USER, constants.VDSM_GROUP))
    fileUtils.chown(mvdb.DB_FILE, constants.VDSM_USER, constants.VDSM_GROUP)


def _db_exists():
    if os.path.isfile(mvdb.DB_FILE):
        return True
    else:
        sys.stdout.write("DB file %s doesn't exists\n" % mvdb.DB_FILE)
        return False


def _db_owned_by_vdsm():
    stat = os.stat(mvdb.DB_FILE)
    actual_uid = stat.st_uid
    actual_gid = stat.st_gid
    expected_uid = fileUtils.resolveUid(constants.VDSM_USER)
    expected_gid = fileUtils.resolveGid(constants.VDSM_GROUP)

    if expected_uid == actual_uid and expected_gid == actual_gid:
        return True
    else:
        sys.stdout.write("DB file %s doesn't have proper ownership %s:%s\n"
                         "Actual ownership is %s:%s\n" %
                         (mvdb.DB_FILE, expected_uid, expected_gid,
                          actual_uid, actual_gid))
        return False


def _db_version_correct():
    version = mvdb.version_info()

    if mvdb.VERSION == version["version"]:
        return True
    else:
        sys.stdout.write("Database version (%s) is not the same as expected "
                         "one (%s)\n" % (version["version"], mvdb.VERSION))
        return False
