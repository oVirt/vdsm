# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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


class DB(object):

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        if self._conn is not _CLOSED_CONNECTION:
            self._conn.close()
            self._conn = _CLOSED_CONNECTION

    def get_volume(self, vol_id):
        for vol in self.iter_volumes([vol_id]):
            return vol

        raise NotFound(vol_id)

    def iter_volumes(self, vol_ids=[]):
        """
        Lookup volumes info in managed volume database for all volume IDs in
        the vol_ids list and returns a list with volume information for each
        volume ID which is present in the database. List is sorted by volume
        IDs. If the list of requested volume IDs is not specified or empty,
        list of all volumes info in the DB is returned. Empty list is returned
        if any of IDs are not in the database.
        """
        # if no volume IDs are provided, select all
        sql = """
            SELECT
                vol_id,
                connection_info,
                path,
                attachment,
                multipath_id
            FROM volumes
        """

        if vol_ids:
            sql += "WHERE vol_id IN ({ids})\n".format(
                ids=",".join("?" for _ in vol_ids))

        sql += "ORDER BY vol_id\n"

        res = self._conn.execute(sql, vol_ids)

        # Fetch all the results now. Iterating over the result set lazily and
        # yielding items one by one can result in
        # sqlite3.OperationalError: unable to close due to unfinalized
        # statements or unfinished backups
        vols = res.fetchall()

        for vol in vols:
            volume_info = {"vol_id": vol["vol_id"]}
            if vol["connection_info"]:
                volume_info["connection_info"] = json.loads(
                    vol["connection_info"])
            if vol["path"]:
                volume_info["path"] = vol["path"]
            if vol["attachment"]:
                volume_info["attachment"] = json.loads(vol["attachment"])
            if vol["multipath_id"]:
                volume_info["multipath_id"] = vol["multipath_id"]
            yield volume_info

    def add_volume(self, vol_id, connection_info):
        sql = """
            INSERT INTO volumes (
                vol_id,
                connection_info)
            VALUES (?, ?)
        """

        log.info("Adding volume %s connection_info=%s",
                 vol_id, connection_info)

        conn_info_json = json.dumps(connection_info).encode("utf-8")

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

        log.info("Updating volume %s path=%s, attachment=%s, multipath_id=%s",
                 vol_id, path, attachment, multipath_id)

        attachment_json = json.dumps(attachment).encode("utf-8")

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

    def version_info(self):
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
        res = self._conn.execute(sql)
        return res.fetchall()[0]


# Private

class _closed_connection(object):

    def __getattr__(self, name):
        raise Closed


_CLOSED_CONNECTION = _closed_connection()
