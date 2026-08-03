# SPDX-FileCopyrightText: oVirt Developers
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Tests for StoragePool.connect() and StoragePool.refresh() releasing a
stale SPM role when the engine reports that this host's master domain
view no longer matches the pool's current master domain (see
_stop_spm_on_master_mismatch).

The scenario this guards against: the engine moved the pool's master
domain to a different storage domain (ReconstructMasterDomainCommand)
while this host was still running as SPM of the OLD master domain.
Both leases are individually valid (sanlock is never violated), so
nothing on the lease-owner level (see spwd) can detect this - only
the host noticing that its own master domain view is no longer what
the engine considers current can.
"""

from types import SimpleNamespace

import pytest

from vdsm.storage import exception as se
from vdsm.storage import sp
from vdsm.storage.sdc import sdCache

from storage.storagefakelib import FakeDomainMonitor
from storage.storagefakelib import FakeTaskManager


@pytest.fixture
def pool():
    return sp.StoragePool("pool-uuid", FakeDomainMonitor(), FakeTaskManager())


def test_stop_spm_on_master_mismatch_when_spm(pool):
    pool.spmRole = sp.SPM_ACQUIRED
    pool.masterDomain = SimpleNamespace(sdUUID="old-master-uuid")
    calls = []
    pool.stopSpm = lambda *a, **kw: calls.append(True)

    pool._stop_spm_on_master_mismatch()

    assert calls == [True]


@pytest.mark.parametrize("role", [sp.SPM_FREE, sp.SPM_CONTEND])
def test_stop_spm_on_master_mismatch_when_not_acquired(pool, role):
    pool.spmRole = role
    pool.masterDomain = SimpleNamespace(sdUUID="old-master-uuid")
    calls = []
    pool.stopSpm = lambda *a, **kw: calls.append(True)

    pool._stop_spm_on_master_mismatch()

    assert calls == []


def test_stop_spm_on_master_mismatch_panics_if_stop_fails(pool, monkeypatch):
    pool.spmRole = sp.SPM_ACQUIRED
    pool.masterDomain = SimpleNamespace(sdUUID="old-master-uuid")

    def failing_stop_spm():
        raise RuntimeError("cluster lock release failed")

    pool.stopSpm = failing_stop_spm

    panics = []
    monkeypatch.setattr(sp, "panic", lambda msg: panics.append(msg))

    pool._stop_spm_on_master_mismatch()

    assert len(panics) == 1


def test_connect_stops_spm_on_wrong_master(pool, monkeypatch):
    pool.spmRole = sp.SPM_ACQUIRED
    pool.masterDomain = SimpleNamespace(sdUUID="old-master-uuid")

    stop_calls = []
    pool.stopSpm = lambda *a, **kw: stop_calls.append(True)

    def raise_unknown_domain(sdUUID):
        raise se.StorageDomainDoesNotExist(sdUUID)

    monkeypatch.setattr(sdCache, "produce", raise_unknown_domain)

    with pytest.raises(se.StoragePoolMasterNotFound):
        pool.connect(hostID=1, msdUUID="new-master-uuid", masterVersion=11)

    assert stop_calls == [True]


def test_connect_does_not_stop_spm_when_not_spm(pool, monkeypatch):
    pool.spmRole = sp.SPM_FREE

    stop_calls = []
    pool.stopSpm = lambda *a, **kw: stop_calls.append(True)

    def raise_unknown_domain(sdUUID):
        raise se.StorageDomainDoesNotExist(sdUUID)

    monkeypatch.setattr(sdCache, "produce", raise_unknown_domain)

    with pytest.raises(se.StoragePoolMasterNotFound):
        pool.connect(hostID=1, msdUUID="new-master-uuid", masterVersion=11)

    assert stop_calls == []


def test_connect_does_not_stop_spm_on_unrelated_error(pool, monkeypatch):
    # A totally unrelated failure (e.g. a transient storage error) must
    # not be treated as a master-domain mismatch: only the two specific
    # exceptions setMasterDomain raises for a real mismatch should
    # trigger releasing the SPM role.
    pool.spmRole = sp.SPM_ACQUIRED
    pool.masterDomain = SimpleNamespace(sdUUID="old-master-uuid")

    stop_calls = []
    pool.stopSpm = lambda *a, **kw: stop_calls.append(True)

    def raise_unrelated(sdUUID):
        raise RuntimeError("unrelated failure")

    monkeypatch.setattr(sdCache, "produce", raise_unrelated)

    with pytest.raises(RuntimeError):
        pool.connect(hostID=1, msdUUID="new-master-uuid", masterVersion=11)

    assert stop_calls == []


def test_refresh_stops_spm_on_wrong_master(pool, monkeypatch):
    # An SPM host's pool is always connected, so engine reconnects take
    # the refresh() path, not connect().
    pool.spmRole = sp.SPM_ACQUIRED
    pool.masterDomain = SimpleNamespace(sdUUID="old-master-uuid")

    stop_calls = []
    pool.stopSpm = lambda *a, **kw: stop_calls.append(True)

    def raise_unknown_domain(sdUUID):
        raise se.StorageDomainDoesNotExist(sdUUID)

    monkeypatch.setattr(sdCache, "produce", raise_unknown_domain)

    with pytest.raises(se.StoragePoolMasterNotFound):
        pool.refresh(msdUUID="new-master-uuid", masterVersion=11)

    assert stop_calls == [True]


def test_refresh_does_not_stop_spm_when_not_spm(pool, monkeypatch):
    pool.spmRole = sp.SPM_FREE

    stop_calls = []
    pool.stopSpm = lambda *a, **kw: stop_calls.append(True)

    def raise_unknown_domain(sdUUID):
        raise se.StorageDomainDoesNotExist(sdUUID)

    monkeypatch.setattr(sdCache, "produce", raise_unknown_domain)

    with pytest.raises(se.StoragePoolMasterNotFound):
        pool.refresh(msdUUID="new-master-uuid", masterVersion=11)

    assert stop_calls == []


def test_refresh_does_not_stop_spm_on_unrelated_error(pool, monkeypatch):
    pool.spmRole = sp.SPM_ACQUIRED
    pool.masterDomain = SimpleNamespace(sdUUID="old-master-uuid")

    stop_calls = []
    pool.stopSpm = lambda *a, **kw: stop_calls.append(True)

    def raise_unrelated(sdUUID):
        raise RuntimeError("unrelated failure")

    monkeypatch.setattr(sdCache, "produce", raise_unrelated)

    with pytest.raises(RuntimeError):
        pool.refresh(msdUUID="new-master-uuid", masterVersion=11)

    assert stop_calls == []
