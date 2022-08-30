# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import logging
import os

from contextlib import closing

from vdsm import constants
from vdsm.common import errors
from vdsm.storage import fileUtils
from vdsm.storage import managedvolumedb as mvdb
from vdsm.tool import LOGGER_NAME

from . import YES, NO


log = logging.getLogger(LOGGER_NAME)


class IncorrectDBVersion(errors.Base):

    msg = "Version of managed volumes database is not correct"


def configure():
    """
    Create database for managed volumes
    """
    if not _db_exists():
        log.info("Creating managed volumes database at %s", mvdb.DB_FILE)
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
        log.info("Managed volume database is already configured")
        return YES
    else:
        log.error("Managed volume database requires configuration")
        return NO


def removeConf():
    """
    Remove database file
    """
    if os.path.isfile(mvdb.DB_FILE):
        log.info("Removing database file %s", mvdb.DB_FILE)
        os.remove(mvdb.DB_FILE)


def _set_db_ownership():
    log.info("Setting up ownership of database file to %s:%s",
             constants.VDSM_USER, constants.VDSM_GROUP)
    fileUtils.chown(mvdb.DB_FILE, constants.VDSM_USER, constants.VDSM_GROUP)


def _db_exists():
    if os.path.isfile(mvdb.DB_FILE):
        return True
    else:
        log.warning("DB file %s doesn't exists", mvdb.DB_FILE)
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
        log.warning("DB file %s doesn't have proper ownership %s:%s",
                    mvdb.DB_FILE, expected_uid, expected_gid)
        log.warning("Actual ownership is %s:%s", actual_uid, actual_gid)
        return False


def _db_version_correct():
    db = mvdb.open()
    with closing(db):
        version = db.version_info()

    if mvdb.VERSION == version["version"]:
        return True
    else:
        log.warning("Database version (%s) is not the same as expected "
                    "one (%s)", version["version"], mvdb.VERSION)
        return False
