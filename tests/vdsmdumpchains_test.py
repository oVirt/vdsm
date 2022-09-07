# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

from vdsm.tool.dump_volume_chains import (_build_volume_chain, _BLANK_UUID,
                                          OrphanVolumes, ChainLoopError,
                                          NoBaseVolume, DuplicateParentError)


def test_empty():
    assert _build_volume_chain([]) == []


def test_only_base_volume():
    assert _build_volume_chain([(_BLANK_UUID, 'a')]) == ['a']


def test_orphan_volumes():
    volumes_children = [(_BLANK_UUID, 'a'), ('a', 'b'), ('c', 'd')]
    with pytest.raises(OrphanVolumes) as cm:
        _build_volume_chain(volumes_children)
    assert cm.value.volumes_children == volumes_children


def test_simple_chain():
    volumes_children = [(_BLANK_UUID, 'a'), ('a', 'b'), ('b', 'c')]
    assert _build_volume_chain(volumes_children) == ['a', 'b', 'c']


def test_loop():
    with pytest.raises(ChainLoopError):
        _build_volume_chain([
            (_BLANK_UUID, 'a'), ('a', 'b'), ('b', 'c'), ('c', 'a')])


def test_no_base_volume():
    with pytest.raises(NoBaseVolume):
        _build_volume_chain([('a', 'b'), ('b', 'c')])


def test_duplicate_parent():
    with pytest.raises(DuplicateParentError):
        _build_volume_chain(
            [(_BLANK_UUID, 'a'), ('a', 'b'), ('a', 'c')])
