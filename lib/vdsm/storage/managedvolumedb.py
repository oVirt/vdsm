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

"""
managevolumedb - stores connection details about managed volumes
"""


from __future__ import absolute_import

import json
import logging
import os
import sqlite3
from contextlib import closing

from vdsm.common import errors
from vdsm.storage.constants import P_VDSM_LIB


VERSION = 1
DB_FILE = os.path.join(P_VDSM_LIB, "managedvolume.db")

log = logging.getLogger("storage.managevolumedb")


class NotFound(errors.Base):

    msg = "Managed volume with vol_id {self.vol_id} not found"

    def __init__(self, vol_id):
        self.vol_id = vol_id


class VolumeAlreadyExists(errors.Base):

    msg = ("Failed to store {self.vol_info}."
           "Volume with id {self.vol_id} already exists in the DB")

    def __init__(self, vol_id, vol_info):
        self.vol_id = vol_id
        self.vol_info = vol_info


class Closed(errors.Base):

    msg = "Operation on closed database connection"


def open():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return DB(conn)


def create_db():
    create_table = """
        CREATE TABLE volumes (
            vol_id TEXT PRIMARY KEY,
            path TEXT,
            connection_info TEXT,
            attachment TEXT,
            multipath_id TEXT,
            updated datetime);

        CREATE UNIQUE INDEX multipath_id ON volumes (multipath_id);

        CREATE TABLE versions (
            version INTEGER PRIMARY KEY,
            description TEXT,
            updated datetime
        );

        INSERT INTO versions (
            version,
            description,
            updated
        )
        VALUES (
            %d,
            "Initial version",
            datetime("now")
        );
    """

    log.info("Initializing managed volume DB in %s", DB_FILE)
    conn = sqlite3.connect(DB_FILE)
    with closing(conn):
        conn.executescript(create_table % VERSION)


def version_info():
    sql = """
        SELECT
            version,
            description,
            updated
        FROM versions
        WHERE version = (
            SELECT max(version) FROM versions
        )
    """

    log.debug("Getting current DB version from %s", DB_FILE)
    conn = sqlite3.connect(DB_FILE)
    with closing(conn):
        conn.row_factory = sqlite3.Row
        res = conn.execute(sql)
        return res.fetchall()[0]


class DB(object):

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        self._conn.close()
        self._conn = ClosedConnection()

    def get_volume(self, vol_id):
        sql = """
            SELECT
                vol_id,
                connection_info,
                path,
                attachment,
                multipath_id
            FROM volumes
            WHERE vol_id = ?
        """

        res = self._conn.execute(sql, (vol_id,))
        vol = res.fetchall()

        if len(vol) < 1:
            raise NotFound(vol_id)

        vol = vol[0]
        volume_info = {}
        if vol["connection_info"]:
            volume_info["connection_info"] = json.loads(vol["connection_info"])
        if vol["path"]:
            volume_info["path"] = vol["path"]
        if vol["attachment"]:
            volume_info["attachment"] = json.loads(vol["attachment"])
        if vol["multipath_id"]:
            volume_info["multipath_id"] = vol["multipath_id"]

        return volume_info

    def add_volume(self, vol_id, connection_info):
        sql = """
            INSERT INTO volumes (
                vol_id,
                connection_info)
            VALUES (?, ?)
        """

        conn_info_json = json.dumps(connection_info).encode("utf-8")

        log.info("Adding volume %s connection_info=%s",
                 vol_id, connection_info)
        try:
            with self._conn:
                self._conn.execute(sql, (vol_id, conn_info_json))
        except sqlite3.IntegrityError:
            raise VolumeAlreadyExists(vol_id, connection_info)

    def remove_volume(self, vol_id):
        sql = "DELETE FROM volumes WHERE vol_id = ?"

        log.info("Removing volume %s", vol_id)
        with self._conn:
            self._conn.execute(sql, (vol_id,))

    def update_volume(self, vol_id, path, attachment, multipath_id):
        sql = """
            UPDATE volumes SET
                path = ?,
                attachment = ?,
                multipath_id = ?,
                updated = datetime('now')
            WHERE vol_id = ?
        """

        attachment_json = json.dumps(attachment).encode("utf-8")

        log.info("Updating volume %s path=%s, attachment=%s, multipath_id=%s",
                 vol_id, path, attachment_json, multipath_id)
        with self._conn:
            self._conn.execute(sql, (path, attachment_json, multipath_id,
                                     vol_id))

    def owns_multipath(self, multipath_id):
        """
        Return True if multipath device is owned by a managed volume.
        """
        sql = """
            SELECT EXISTS (
                SELECT 1
                FROM volumes
                WHERE multipath_id = ?
            )
        """
        res = self._conn.execute(sql, (multipath_id,))
        row = res.fetchall()[0]
        return row[0] == 1


class ClosedConnection(object):

    def __getattr__(self, name):
        raise Closed
