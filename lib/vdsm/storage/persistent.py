# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
persistent module provides generic class with common verification and
validation functionality implemented.
"""

from __future__ import absolute_import
import hashlib
import logging
from contextlib import contextmanager

import six

from vdsm.storage import exception as se

import threading
from copy import deepcopy
from six.moves import filter as ifilter

SHA_CKSUM_TAG = "_SHA_CKSUM"

log = logging.getLogger("storage.persistent")


def _format_line(key, value):
    return "%s=%s" % (key, value)


def _dump_lines(md):
    return [_format_line(key, value)
            for key, value in sorted(six.iteritems(md))]


def _calc_checksum(lines):
    h = hashlib.sha1()
    for line in lines:
        h.update(line.encode('ascii', 'xmlcharrefreplace'))
    return h.hexdigest()


def _parse_lines(lines):
    md = {}
    for line in lines:
        try:
            key, value = line.split("=", 1)
        except ValueError:
            log.warning("Could not parse line: %r", line)
        else:
            md[key.strip()] = value
    return md


def unicodeEncoder(s):
    return s


def unicodeDecoder(s):
    return s


class DictValidator(object):
    def __init__(self, dictObj, validatorDict):
        self._dict = dictObj
        self._validatorDict = validatorDict

        # Fields to export as is
        self.transaction = self._dict.transaction
        self.invalidate = self._dict.invalidate

    def __len__(self):
        return len(self.keys())

    def __contains__(self, item):
        return (item in self._validatorDict and item in self._dict)

    def _validator(self, key):
        if key in self._validatorDict:
            return self._validatorDict[key]

        for entry in self._validatorDict:
            if hasattr(entry, "match"):
                if entry.match(key) is not None:
                    return self._validatorDict[entry]

        raise KeyError("%s not in allowed keys list" % key)

    def _encoder(self, key):
        return self._validator(key)[1]

    def _decoder(self, key):
        return self._validator(key)[0]

    def __getitem__(self, key):
        dec = self._decoder(key)
        return dec(self._dict[key])

    def get(self, key, default=None):
        dec = self._decoder(key)
        try:
            return dec(self._dict[key])
        except KeyError:
            return default

    def __setitem__(self, key, value):
        enc = self._encoder(key)
        self._dict.__setitem__(key, enc(value))

    def __delitem__(self, key):
        del self._dict[key]

    def __iter__(self):
        return ifilter(lambda k: k in self._validatorDict,
                       self._dict.__iter__())

    def keys(self):
        return list(self.__iter__())

    def iterkeys(self):
        return self.__iter__()

    def update(self, metadata):
        metadata = metadata.copy()
        for key, value in six.iteritems(metadata):
            enc = self._encoder(key)
            metadata[key] = enc(value)

        self._dict.update(metadata)

    def clear(self):
        for key in self._validatorDict:
            if key in self._dict:
                del self._dict[key]

    def copy(self):
        md = self._dict.copy()
        for key, value in six.iteritems(md):
            try:
                dec = self._decoder(key)
                md[key] = dec(value)
            except KeyError:
                # there is a value in the dict that isn't mine, skipping
                pass

        return md


class PersistentDict(object):
    """
    This class provides interface for a generic set of key=value pairs
    that can be accessed by any consumer
    """

    @contextmanager
    def _accessWrapper(self):
        with self._syncRoot:
            if not self._isValid:
                self._refresh()

            yield

    @contextmanager
    def transaction(self):
        with self._syncRoot:
            if self._inTransaction:
                log.debug("Reusing active transaction")
                yield
                return

            self._inTransaction = True
            try:
                with self._accessWrapper():
                    log.debug("Starting transaction")
                    backup = deepcopy(self._metadata)
                    try:
                        yield
                        # TODO : check appropriateness
                        if backup != self._metadata:
                            log.debug("Flushing changes")
                            self._flush(self._metadata)
                        log.debug("Finished transaction")
                    except:
                        log.warning(
                            "Error in transaction, rolling back changes",
                            exc_info=True)
                        # TBD: Maybe check that the old MD is what I remember?
                        self._metadata = backup
                        raise
            finally:
                self._inTransaction = False

    def __init__(self, metaReaderWriter):
        self._syncRoot = threading.RLock()
        self._metadata = {}
        self._metaRW = metaReaderWriter
        self._isValid = False
        self._inTransaction = False
        log.debug("Created a persistent dict with %s backend",
                  self._metaRW.__class__.__name__)

    def get(self, key, default=None):
        with self._accessWrapper():
            return self._metadata.get(key, default)

    def __getitem__(self, key):
        with self._accessWrapper():
            return self._metadata[key]

    def __setitem__(self, key, value):
        with self.transaction():
            self._metadata[key] = value

    def __delitem__(self, key):
        with self.transaction():
            del self._metadata[key]

    def update(self, metadata):
        with self.transaction():
            self._metadata.update(metadata)

    def __iter__(self):
        with self._accessWrapper():
            return iter(self._metadata)

    def _refresh(self):
        with self._syncRoot:
            lines = self._metaRW.readlines()

            log.debug("read lines (%s)=%s",
                      self._metaRW.__class__.__name__,
                      lines)

            newMD = _parse_lines(lines)
            declaredChecksum = newMD.pop(SHA_CKSUM_TAG, None)
            if not newMD:
                log.debug("Empty metadata")
                self._isValid = True
                self._metadata = newMD
                return

            if declaredChecksum is None:
                # No checksum in the metadata, let it through as is
                # FIXME : This is ugly but necessary, What we need is a class
                # method that creates the initial metadata. Then we can assume
                # that empty metadata is always invalid.
                log.warning("data has no embedded checksum - "
                            "trust it as it is")
                self._isValid = True
                self._metadata = newMD
                return

            computedChecksum = _calc_checksum(_dump_lines(newMD))

            if declaredChecksum != computedChecksum:
                log.warning("data seal is broken metadata declares `%s` "
                            "should be `%s` (lines=%s)",
                            declaredChecksum, computedChecksum, newMD)
                raise se.MetaDataSealIsBroken(declaredChecksum,
                                              computedChecksum)

            self._isValid = True
            self._metadata = newMD

    def _flush(self, overrideMD):
        with self._syncRoot:
            md = overrideMD
            lines = _dump_lines(md)
            computedChecksum = _calc_checksum(lines)
            lines.append(_format_line(SHA_CKSUM_TAG, str(computedChecksum)))
            log.debug("about to write lines (%s)=%s",
                      self._metaRW.__class__.__name__, lines)
            self._metaRW.writelines(lines)

            self._metadata = md
            self._isValid = True

    def invalidate(self):
        with self._syncRoot:
            self._isValid = False

    def __len__(self):
        with self._accessWrapper():
            return len(self._metadata)

    def __contains__(self, item):
        with self._accessWrapper():
            return item in self._metadata

    def copy(self):
        with self._accessWrapper():
            return self._metadata.copy()

    def clear(self):
        with self.transaction():
            self._metadata.clear()
