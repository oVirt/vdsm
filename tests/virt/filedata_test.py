# Copyright 2020 Red Hat, Inc.
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
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Refer to the README and COPYING files for full details of the license.

from collections import namedtuple
from contextlib import contextmanager
import os
import stat
import tempfile
import time

import pytest

from vdsm.common import exception
from vdsm.supervdsm_api import virt
from vdsm.virt import filedata


# File data


FILE_DATA = 'hello'
FILE_DATA_2 = 'world'
ENCODED_DATA = 'aGVsbG8=\n'
DIRECTORY_MODE = stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IXOTH
UUID = '12345678-1234-1234-1234-1234567890ab'


def test_file_data_read():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'test')
        open(path, 'w').write(FILE_DATA)
        data = filedata.FileData(path)
        assert data.retrieve() == ENCODED_DATA


def test_file_data_write():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'test')
        data = filedata.FileData(path)
        data.store(ENCODED_DATA)
        assert open(path).read() == FILE_DATA


def test_file_data_modified():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'test')
        open(path, 'w').write(FILE_DATA)
        data = filedata.FileData(path)
        assert data.last_modified() == os.stat(path).st_mtime


@pytest.mark.parametrize("last_modified, is_none", [
    pytest.param(
        0,
        False,
        id="forced read"
    ),
    pytest.param(
        time.time() - 0.1,  # file mtime may differ from system time a bit
        False,
        id="new data"
    ),
    pytest.param(
        time.time() + 1000,
        False,
        id="future time"
    ),
    pytest.param(
        None,
        True,
        id="current data"
    ),
])
def test_file_data_conditional_read(last_modified, is_none):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'test')
        open(path, 'w').write(FILE_DATA)
        data = filedata.FileData(path)
        if last_modified is None:
            last_modified = data.last_modified()
        encoded = data.retrieve(last_modified=last_modified)
        if is_none:
            assert encoded is None
        else:
            assert encoded == ENCODED_DATA


# Directory data


Paths = namedtuple("Paths", ['directory', 'path', 'subdirectory', 'subpath'])


@contextmanager
def temporary_directory(monkeypatch=None):
    with tempfile.TemporaryDirectory() as d:
        directory = os.path.join(d, UUID)
        path = os.path.join(directory, 'file1')
        subdirectory = os.path.join(directory, 'data')
        subpath = os.path.join(subdirectory, 'file2')
        if monkeypatch is not None:
            monkeypatch.setattr(filedata.constants, 'P_LIBVIRT_SWTPM',
                                os.path.dirname(directory))
        yield Paths(directory=directory,
                    path=path, subdirectory=subdirectory, subpath=subpath)


@contextmanager
def directory_data(monkeypatch=None):
    with temporary_directory(monkeypatch) as d:
        os.mkdir(d.directory)
        os.chmod(d.directory, DIRECTORY_MODE)
        os.mkdir(d.subdirectory)
        open(d.path, 'w').write(FILE_DATA)
        open(d.subpath, 'w').write(FILE_DATA_2)
        yield d


def test_directory_data_read_write():
    with directory_data() as d:
        data = filedata.DirectoryData(d.directory)
        encoded = data.retrieve()
        assert encoded is not None
    with temporary_directory() as d:
        data = filedata.DirectoryData(d.directory)
        data.store(encoded)
        assert open(d.path).read() == FILE_DATA
        assert open(d.subpath).read() == FILE_DATA_2
        n = 0
        for _root, _dirs, files in os.walk(d.directory):
            n += len(files)
        assert n == 2
        permissions = stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO
        assert os.stat(d.directory).st_mode & permissions == DIRECTORY_MODE


def test_directory_data_rewrite():
    with directory_data() as d:
        data = filedata.DirectoryData(d.directory)
        encoded = data.retrieve()
    with temporary_directory() as d:
        os.mkdir(d.directory)
        old_path = os.path.join(d.directory, 'old')
        open(old_path, 'w').write("invalid")
        open(d.path, 'w').write("invalid")
        data = filedata.DirectoryData(d.directory)
        data.store(encoded)
        assert not os.path.exists(old_path)
        assert open(d.path).read() == FILE_DATA
        assert open(d.subpath).read() == FILE_DATA_2
        n = 0
        for _root, _dirs, files in os.walk(d.directory):
            n += len(files)
        assert n == 2


def test_directory_data_modified():
    with directory_data() as d:
        data = filedata.DirectoryData(d.directory)
        data.retrieve()
        assert data.last_modified() == \
            max(os.stat(d.path).st_mtime, os.stat(d.subpath).st_mtime)


def test_no_data():
    data = filedata.DirectoryData('/this-directory-must-not-exist')
    with pytest.raises(exception.ExternalDataFailed):
        data.retrieve()


# Monitor


def data_retriever(directory):
    data = filedata.DirectoryData(directory)

    def retriever(last_modified):
        encoded = data.retrieve(last_modified=last_modified)
        return encoded, data.last_modified()
    return retriever


def test_monitor_read():
    with directory_data() as d:
        monitor = filedata.Monitor(data_retriever(d.directory))
        encoded = monitor.data()
        assert encoded is not None
    with temporary_directory() as d:
        data = filedata.DirectoryData(d.directory)
        data.store(encoded)
        assert open(d.path).read() == FILE_DATA
        assert open(d.subpath).read() == FILE_DATA_2
        n = 0
        for _root, _dirs, files in os.walk(d.directory):
            n += len(files)
        assert n == 2


def test_monitor_repeated_read():
    with directory_data() as d:
        monitor = filedata.Monitor(data_retriever(d.directory))
        data = monitor.data()
        hash_ = monitor.data_hash()
        assert data is not None
        assert hash_ is not None
        assert monitor.data() is None
        assert monitor.data_hash() == hash_
        assert monitor.data(force=True) == data
        assert monitor.data_hash() == hash_


def test_monitor_data_change():
    with directory_data() as d:
        monitor = filedata.Monitor(data_retriever(d.directory))
        data = monitor.data()
        hash_ = monitor.data_hash()
        open(d.subpath, 'a').write('\n')
        new_data = monitor.data()
        new_hash = monitor.data_hash()
        assert new_data is not None
        assert new_data != data
        assert new_hash is not None
        assert new_hash != hash_
        assert monitor.data() is None
        assert monitor.data_hash() == new_hash


def test_monitor_no_data():
    retriever = data_retriever('/this-directory-must-not-exist')
    monitor = filedata.Monitor(retriever)
    with pytest.raises(exception.ExternalDataFailed):
        monitor.data()


# Supervdsm API


def test_supervdsm_read_write(monkeypatch):
    with directory_data(monkeypatch):
        encoded, _modified = virt.read_tpm_data(UUID, -1)
        assert encoded
    with temporary_directory(monkeypatch):
        virt.write_tpm_data(UUID, encoded)
        assert encoded == virt.read_tpm_data(UUID, -1)[0]


def test_supervdsm_invalid_vmid(monkeypatch):
    with directory_data(monkeypatch):
        encoded, _modified = virt.read_tpm_data(UUID, -1)
    with pytest.raises(exception.ExternalDataFailed):
        virt.write_tpm_data('../foo', encoded)


def test_supervdsm_symlink(monkeypatch):
    with directory_data(monkeypatch) as d:
        os.symlink('/foo', os.path.join(d.directory, 'bar'))
        encoded = filedata.DirectoryData(d.directory).retrieve()
    with temporary_directory(monkeypatch):
        with pytest.raises(exception.ExternalDataFailed):
            virt.write_tpm_data(UUID, encoded)
