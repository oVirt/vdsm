# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from collections import namedtuple
from enum import Enum
import hashlib
import threading

from vdsm.common import exception
from vdsm.virt import filedata


class ExternalDataKind(Enum):
    # Value is the string used in API
    TPM = "tpm"
    NVRAM = "nvram"


class ExternalData(object):
    Data = namedtuple("Data", ["stable_data", "current_data",
                               "monitor_hash", "engine_hash"])

    @staticmethod
    def secure_hash(data):
        sha256 = hashlib.sha256()
        sha256.update(data.encode('ascii'))
        return sha256.hexdigest()

    def __init__(self, kind, log, read_function, initial_data, engine_hash):
        self._data = ExternalData.Data(stable_data=initial_data,
                                       current_data=initial_data,
                                       monitor_hash=hash(initial_data),
                                       engine_hash=engine_hash)
        self._kind = kind
        self._lock = threading.Lock()
        self._log = log
        self._monitor = filedata.Monitor(read_function)

    @property
    def data(self):
        return self._data

    def update(self, force=False):
        """
        Update and return data and its hash.

        The returned data is the last known stable data (None if there is no
        such data yet), unless `force` is true, in which case the most recent
        data is considered being stable and returned.

        :param force: iff true then force data update even if the data seems
          to be unchanged and always return fresh data, even if it is not
          known to be stable
        :type force: boolean
        :returns: pair (DATA, HASH) where DATA is the DATA itself or None if
          there is no data yet and HASH is its cryptographic hash or None if
          there is no data yet
        :rtype: pair (string or None, string or None)
        """
        with self._lock:
            data = self._update_internal(force=force)
            self._data = data
            return data.stable_data, data.engine_hash

    def _update_internal(self, force=False):
        error = None
        try:
            new_data = self._monitor.data(force=force)
        except Exception as e:
            self._log.error("Failed to read %s data: %s", self._kind, e)
            if isinstance(e, exception.ExternalDataFailed):
                raise
            else:
                # Let's not leak data from the exception
                error = e
        if error is not None:
            raise exception.ExternalDataFailed(
                reason="Failed to read %s data" % self._kind,
                exception=error
            )
        monitor_hash = self._monitor.data_hash()
        if new_data is None:
            # Data is unchanged, we can report it
            stable_data = self._data.current_data
            if stable_data is not self._data.stable_data:
                data = self._data._replace(
                    stable_data=stable_data,
                    engine_hash=ExternalData.secure_hash(stable_data)
                )
            else:
                # No change at all
                data = self._data
        elif force or monitor_hash == self._data.monitor_hash:
            # New stable data, replace old data completely
            data = ExternalData.Data(
                stable_data=new_data,
                current_data=new_data,
                monitor_hash=monitor_hash,
                engine_hash=ExternalData.secure_hash(new_data)
            )
        else:
            # New unstable data, store it but don't report it
            data = self._data._replace(
                current_data=new_data,
                monitor_hash=monitor_hash
            )
        return data
