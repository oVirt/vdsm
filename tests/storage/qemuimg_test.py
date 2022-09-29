# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import io
import json
import os
import pprint

from collections import namedtuple
from functools import partial

import pytest

from monkeypatch import MonkeyPatch, MonkeyPatchScope

from . import qemuio

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common import constants
from vdsm.common import exception
from vdsm.common.units import KiB, MiB, GiB
from vdsm.storage import qemuimg

from testlib import make_config
from testlib import namedTemporaryDir
from testlib import temporaryPath

from . marks import requires_root

CLUSTER_SIZE = 64 * KiB

QEMU_IMG = qemuimg._qemuimg.cmd

CONFIG = make_config([('irs', 'qcow2_compat', '0.10')])


def fake_json_call(data, cmd, **kw):
    return 0, json.dumps(data).encode("utf-8"), []


class TestCompat:

    @pytest.mark.parametrize("compat,result", [
        ("0.10", True),
        ("1.1", True),
        ("10.1", False),
    ])
    def test_supports_compat(self, compat, result):
        assert result == qemuimg.supports_compat(compat)


class TestInfo:

    CLUSTER_SIZE = 65536

    def _fake_info(self):
        return {
            "virtual-size": MiB,
            "filename": "leaf.img",
            "cluster-size": self.CLUSTER_SIZE,
            "format": "qcow2",
            "actual-size": 200704,
            "format-specific": {
                "type": "qcow2",
                "data": {
                    "compat": "1.1",
                    "lazy-refcounts": False,
                    "refcount-bits": 16,
                    "corrupt": False
                }
            },
            "backing-filename": "/var/tmp/test.img",
            "backing-filename-format": "raw",
            "dirty-flag": False
        }

    def test_info(self):
        with namedTemporaryDir() as tmpdir:
            base_path = os.path.join(tmpdir, 'base.img')
            leaf_path = os.path.join(tmpdir, 'leaf.img')
            size = MiB
            leaf_fmt = qemuimg.FORMAT.QCOW2
            with MonkeyPatchScope([(qemuimg, 'config', CONFIG)]):
                op = qemuimg.create(base_path,
                                    size=size,
                                    format=qemuimg.FORMAT.RAW)
                op.run()
                op = qemuimg.create(leaf_path,
                                    format=leaf_fmt,
                                    backing=base_path,
                                    backingFormat="raw")
                op.run()

            info = qemuimg.info(leaf_path)
            assert leaf_fmt == info['format']
            assert size == info['virtual-size']
            assert self.CLUSTER_SIZE == info['cluster-size']
            assert base_path == info['backing-filename']
            assert "raw" == info['backing-filename-format']
            assert '0.10' == info['format-specific']['data']['compat']

    @pytest.mark.parametrize("unsafe", [True, False])
    def test_unsafe_info(self, unsafe):
        with namedTemporaryDir() as tmpdir:
            img = os.path.join(tmpdir, 'img.img')
            size = MiB
            op = qemuimg.create(img, size=size, format=qemuimg.FORMAT.QCOW2)
            op.run()
            info = qemuimg.info(img, unsafe=unsafe)
            assert size == info['virtual-size']

    def test_parse_error(self):
        def call(cmd, **kw):
            out = b"image: leaf.img\ninvalid file format line"
            return 0, out, ""

        with MonkeyPatchScope([(commands, "execCmd", call)]):
            with pytest.raises(cmdutils.Error):
                qemuimg.info('leaf.img')

    @pytest.mark.parametrize("field", ['format', 'virtual-size'])
    def test_missing_required_field_raises(self, field):
        data = self._fake_info()
        del data[field]
        with MonkeyPatchScope([(commands, "execCmd",
                                partial(fake_json_call, data))]):
            with pytest.raises(cmdutils.Error):
                qemuimg.info('leaf.img')

    def test_untrusted_image(self):
        with namedTemporaryDir() as tmpdir:
            img = os.path.join(tmpdir, 'untrusted.img')
            size = 500 * GiB
            op = qemuimg.create(img, size=size, format=qemuimg.FORMAT.QCOW2)
            op.run()
            info = qemuimg.info(img, trusted_image=False)
            assert size == info['virtual-size']

    def test_untrusted_image_call(self):
        command = []

        def call(cmd, *args, **kwargs):
            command.extend(cmd)
            out = json.dumps(self._fake_info()).encode("utf-8")
            return 0, out, b""

        with MonkeyPatchScope([(commands, "execCmd", call)]):
            qemuimg.info('unused', trusted_image=False)

        assert command[:3] == [constants.EXT_PRLIMIT,
                               '--cpu=30',
                               '--as=1073741824']

    def test_backing_chain(self, tmpdir):
        virtual_size = 10 * MiB
        base = str(tmpdir.join('base.raw'))
        op = qemuimg.create(
            base,
            size=virtual_size,
            format=qemuimg.FORMAT.RAW)
        op.run()

        mid = str(tmpdir.join('mid.qcow2'))
        op = qemuimg.create(
            mid,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat="1.1",
            backing=base,
            backingFormat="raw")
        op.run()

        op = qemuimg.bitmap_add(mid, "bitmap")
        op.run()

        top = str(tmpdir.join('top.qcow2'))
        op = qemuimg.create(
            top,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat="1.1",
            backing=mid,
            backingFormat="qcow2")
        op.run()

        op = qemuimg.bitmap_add(top, "bitmap")
        op.run()

        chain_info = qemuimg.info(top, backing_chain=True)

        assert len(chain_info) == 3

        # Check top image info.

        top_info = chain_info[0]

        assert top_info["virtual-size"] == virtual_size
        assert top_info["format"] == "qcow2"
        assert top_info["backing-filename"] == mid
        assert top_info["backing-filename-format"] == "qcow2"

        assert "actual-size" in top_info
        assert "cluster-size" in top_info

        qcow2_data = top_info["format-specific"]["data"]
        assert qcow2_data["compat"] == "1.1"
        assert len(qcow2_data["bitmaps"]) == 1

        # Check middle image info.

        mid_info = chain_info[1]

        assert mid_info["virtual-size"] == virtual_size
        assert mid_info["format"] == "qcow2"
        assert mid_info["backing-filename"] == base
        assert mid_info["backing-filename-format"] == "raw"

        assert "actual-size" in mid_info
        assert "cluster-size" in mid_info

        qcow2_data = mid_info["format-specific"]["data"]
        assert qcow2_data["compat"] == "1.1"
        assert len(qcow2_data["bitmaps"]) == 1

        # Check base image info.

        base_info = chain_info[2]

        assert base_info["virtual-size"] == virtual_size
        assert base_info["format"] == "raw"
        assert "actual-size" in base_info

    def test_backing_chain_single(self, tmpdir):
        virtual_size = 10 * MiB
        img = str(tmpdir.join("single.qcow2"))
        op = qemuimg.create(
            img,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat="1.1")
        op.run()

        chain_info = qemuimg.info(img, backing_chain=True)

        assert len(chain_info) == 1

        img_info = chain_info[0]

        assert img_info["virtual-size"] == virtual_size
        assert img_info["format"] == "qcow2"
        assert "actual-size" in img_info
        assert "cluster-size" in img_info

        qcow2_data = img_info["format-specific"]["data"]
        assert qcow2_data["compat"] == "1.1"


class TestCreate:

    @pytest.mark.parametrize("preallocation", [
        qemuimg.PREALLOCATION.FALLOC,
        qemuimg.PREALLOCATION.FULL,
    ])
    def test_preallocation(self, preallocation):
        virtual_size = 10 * MiB
        with temporaryPath() as image:
            op = qemuimg.create(
                image,
                size=virtual_size,
                format=qemuimg.FORMAT.RAW,
                preallocation=preallocation)
            op.run()
            check_raw_preallocated_image(image, virtual_size)

    @pytest.mark.parametrize("preallocation", [
        None,
        qemuimg.PREALLOCATION.OFF
    ])
    def test_preallocation_off(self, preallocation):
        virtual_size = 10 * MiB
        with temporaryPath() as image:
            op = qemuimg.create(
                image,
                size=virtual_size,
                format=qemuimg.FORMAT.RAW,
                preallocation=preallocation)
            op.run()
            check_raw_sparse_image(image, virtual_size)

    def test_no_format(self):
        size = 4 * KiB
        with namedTemporaryDir() as tmpdir:
            image = os.path.join(tmpdir, "image")
            op = qemuimg.create(image, size=size)
            op.run()

            info = qemuimg.info(image)
            assert info['format'] == qemuimg.FORMAT.RAW
            assert info['virtual-size'] == size

    def test_zero_size(self):
        with namedTemporaryDir() as tmpdir:
            image = os.path.join(tmpdir, "image")
            op = qemuimg.create(image, size=0)
            op.run()

            info = qemuimg.info(image)
            assert info['format'] == qemuimg.FORMAT.RAW
            assert info['virtual-size'] == 0

    def test_qcow2_compat(self):
        with namedTemporaryDir() as tmpdir:
            image = os.path.join(tmpdir, "image")
            size = 10 * GiB
            op = qemuimg.create(image, format='qcow2', size=size)
            op.run()

            info = qemuimg.info(image)
            assert info['format'] == qemuimg.FORMAT.QCOW2
            assert info['format-specific']['data']['compat'] == "0.10"
            assert info['virtual-size'] == size

    def test_qcow2_compat_version3(self):
        with namedTemporaryDir() as tmpdir:
            image = os.path.join(tmpdir, "image")
            size = 10 * GiB
            op = qemuimg.create(image, format='qcow2',
                                qcow2Compat='1.1', size=size)
            op.run()

            info = qemuimg.info(image)
            assert info['format'] == qemuimg.FORMAT.QCOW2
            assert info['format-specific']['data']['compat'] == "1.1"
            assert info['virtual-size'] == size

    def test_qcow2_compat_invalid(self):
        with pytest.raises(ValueError):
            qemuimg.create('image', format='qcow2', qcow2Compat='1.11')

    def test_invalid_config(self):
        config = make_config([('irs', 'qcow2_compat', '1.2')])
        with MonkeyPatchScope([(qemuimg, 'config', config)]):
            with pytest.raises(exception.InvalidConfiguration):
                qemuimg.create('image', format='qcow2')

    @MonkeyPatch(qemuimg, 'config', CONFIG)
    def test_unsafe_create_volume(self):
        with namedTemporaryDir() as tmpdir:
            path = os.path.join(tmpdir, 'test.qcow2')
            # Using unsafe=True to verify that it is possible to create an
            # image based on a non-existing backing file, like an inactive LV.
            qemuimg.create(path, size=MiB, format=qemuimg.FORMAT.QCOW2,
                           backing='no-such-file', unsafe=True)


class TestConvert:

    def test_no_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        'src', 'dst']
            assert cmd == expected

        with MonkeyPatchScope([(qemuimg, 'ProgressCommand', convert)]):
            qemuimg.convert('src', 'dst')

    def test_no_create(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        '-n', 'src', 'dst']
            assert cmd == expected

        with MonkeyPatchScope([(qemuimg, 'ProgressCommand', convert)]):
            qemuimg.convert('src', 'dst', create=False)

    def test_qcow2_compat(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        '-O', 'qcow2', '-o', 'compat=0.10', 'src', 'dst']
            assert cmd == expected

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'ProgressCommand', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2')

    def test_qcow2_compat_version3(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        '-O', 'qcow2', '-o', 'compat=1.1', 'src', 'dst']
            assert cmd == expected

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'ProgressCommand', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2',
                            dstQcow2Compat='1.1')

    def test_qcow2_no_backing_file(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        '-O', 'qcow2', '-o', 'compat=0.10', 'src', 'dst']
            assert cmd == expected

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'ProgressCommand', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2')

    def test_qcow2_backing_file(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        '-O', 'qcow2', '-o', 'compat=0.10,backing_file=bak',
                        'src', 'dst']
            assert cmd == expected

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'ProgressCommand', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2',
                            backing='bak')

    def test_qcow2_backing_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        '-O', 'qcow2', '-o', 'compat=0.10', 'src', 'dst']
            assert cmd == expected

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'ProgressCommand', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2',
                            backingFormat='qcow2')

    def test_qcow2_backing_file_and_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        '-O', 'qcow2', '-o',
                        'compat=0.10,backing_file=bak,backing_fmt=qcow2',
                        'src', 'dst']
            assert cmd == expected

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'ProgressCommand', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2',
                            backing='bak', backingFormat='qcow2')

    def test_qcow2_compat_invalid(self):
        with pytest.raises(ValueError):
            qemuimg.convert('image', 'dst', dstFormat='qcow2',
                            backing='bak', backingFormat='qcow2',
                            dstQcow2Compat='1.11')

    @pytest.mark.parametrize("dst_compat,create", [
        pytest.param("0.10", True),
        pytest.param("1.1", False),
    ])
    def test_qcow2(self, tmp_mount, dst_compat, create):
        virtual_size = 10 * MiB

        # Create source chain.
        src_base = os.path.join(tmp_mount.path, 'src_base.img')
        op = qemuimg.create(
            src_base,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1'
        )
        op.run()

        src_top = os.path.join(tmp_mount.path, 'src_top.img')
        op = qemuimg.create(
            src_top,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1',
            backing=src_base,
            backingFormat='qcow2'
        )
        op.run()

        # Create dest chain
        dst_base = os.path.join(tmp_mount.path, 'dst_base.img')
        op = qemuimg.create(
            dst_base,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat=dst_compat,
        )
        op.run()

        dst_top = os.path.join(tmp_mount.path, 'dst_top.img')
        op = qemuimg.create(
            dst_top,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat=dst_compat,
            backing=dst_base,
            backingFormat='qcow2'
        )
        op.run()

        # Write data to the source chain.
        for i, path in enumerate([src_base, src_top]):
            qemuio.write_pattern(
                path,
                "qcow2",
                offset=i * MiB,
                len=64 * KiB,
                pattern=0xf0 + i)

        # Copy base to base.
        op = qemuimg.convert(
            src_base,
            dst_base,
            srcFormat='qcow2',
            dstFormat='qcow2',
            dstQcow2Compat=dst_compat,
            create=create,
            target_is_zero=True,
        )
        op.run()

        # Copy top to top.
        op = qemuimg.convert(
            src_top,
            dst_top,
            srcFormat='qcow2',
            dstFormat='qcow2',
            backing=dst_base,
            backingFormat='qcow2',
            dstQcow2Compat=dst_compat,
            # With a backing we can always use False.
            create=False
        )
        op.run()

        # Run comparisons, if there is a mismatch in content or size
        # op.run() will raise and fail the test.

        op = qemuimg.compare(
            src_base,
            dst_base,
            img1_format='qcow2',
            img2_format='qcow2',
            strict=True,
        )
        op.run()

        op = qemuimg.compare(
            src_top,
            dst_top,
            img1_format='qcow2',
            img2_format='qcow2',
            strict=True
        )
        op.run()

    @pytest.mark.parametrize("dst_compat,create", [
        pytest.param("0.10", True),
        pytest.param("1.1", False),
    ])
    def test_qcow2_collapsed(self, tmp_mount, dst_compat, create):
        virtual_size = 10 * MiB

        # Create empty source chain.
        src_base = os.path.join(tmp_mount.path, 'src_base.img')
        op = qemuimg.create(
            src_base,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1'
        )
        op.run()

        src_top = os.path.join(tmp_mount.path, 'src_top.img')
        op = qemuimg.create(
            src_top,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1',
            backing=src_base,
            backingFormat='qcow2'
        )
        op.run()

        # Write data to the source chain.
        for i, path in enumerate([src_base, src_top]):
            qemuio.write_pattern(
                path,
                "qcow2",
                offset=i * MiB,
                len=64 * KiB,
                pattern=0xf0 + i)

        # Create destination image.
        dst = os.path.join(tmp_mount.path, 'dst.img')
        op = qemuimg.create(
            dst,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat=dst_compat,
        )
        op.run()

        # Copy src chain to dst.
        op = qemuimg.convert(
            src_top,
            dst,
            srcFormat='qcow2',
            dstFormat='qcow2',
            dstQcow2Compat=dst_compat,
            create=create,
            target_is_zero=True,
        )
        op.run()

        op = qemuimg.compare(
            src_top,
            dst,
            img1_format='qcow2',
            img2_format='qcow2',
            strict=True,
        )
        op.run()

    def test_raw_preallocated(self, tmp_mount):
        virtual_size = 2 * MiB

        # Create source image.
        src = os.path.join(tmp_mount.path, 'src.img')
        op = qemuimg.create(
            src,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1'
        )
        op.run()

        # Write data to the source image.
        qemuio.write_pattern(
            src,
            "qcow2",
            offset=MiB,
            len=64 * KiB,
            pattern=0xf0)

        # Create preallocated destination image.
        dst = os.path.join(tmp_mount.path, 'dst.img')
        op = qemuimg.create(
            dst,
            size=virtual_size,
            format=qemuimg.FORMAT.RAW,
            preallocation=qemuimg.PREALLOCATION.FALLOC,
        )
        op.run()

        # Convert src to dst.
        op = qemuimg.convert(
            src,
            dst,
            srcFormat=qemuimg.FORMAT.QCOW2,
            dstFormat=qemuimg.FORMAT.RAW,
            create=False,
            target_is_zero=True,
        )
        op.run()

        # Compare content - since we copied to preallocated image, allocation
        # cannot be compared.
        op = qemuimg.compare(
            src,
            dst,
            img1_format=qemuimg.FORMAT.QCOW2,
            img2_format=qemuimg.FORMAT.RAW,
            strict=False,
        )
        op.run()

        # Check that dst is still preallocated.
        info = qemuimg.info(dst)
        assert info["virtual-size"] == info["actual-size"]

    def test_copy_bitmaps(self, tmp_mount):
        virtual_size = MiB
        base_bitmaps = ['base_bitmap1', 'base_bitmap2']
        top_bitmaps = ['top_bitmap1', 'top_bitmap2']

        # Create source chain.
        src_base = os.path.join(tmp_mount.path, 'src_base.img')
        op = qemuimg.create(
            src_base,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1'
        )
        op.run()

        # Add new bitmaps to src_base
        for name in base_bitmaps:
            op = qemuimg.bitmap_add(src_base, name)
            op.run()

        src_top = os.path.join(tmp_mount.path, 'src_top.img')
        op = qemuimg.create(
            src_top,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1',
            backing=src_base,
            backingFormat='qcow2'
        )
        op.run()

        # Add new bitmaps to src_top
        for name in top_bitmaps:
            op = qemuimg.bitmap_add(src_top, name)
            op.run()

        # Create destination chain.
        dst_base = os.path.join(tmp_mount.path, 'dst_base.img')
        op = qemuimg.create(
            dst_base,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1'
        )
        op.run()

        dst_top = os.path.join(tmp_mount.path, 'dst_top.img')
        op = qemuimg.create(
            dst_top,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1',
            backing=dst_base,
            backingFormat='qcow2'
        )
        op.run()

        # Convert src_base to dst_base with bitmaps
        op = qemuimg.convert(
            src_base,
            dst_base,
            srcFormat=qemuimg.FORMAT.QCOW2,
            dstFormat=qemuimg.FORMAT.QCOW2,
            dstQcow2Compat='1.1',
            bitmaps=True
        )
        op.run()

        # Convert src_top to dst_top with bitmaps
        op = qemuimg.convert(
            src_top,
            dst_top,
            srcFormat=qemuimg.FORMAT.QCOW2,
            dstFormat=qemuimg.FORMAT.QCOW2,
            dstQcow2Compat='1.1',
            bitmaps=True
        )
        op.run()

        # Verify that all layers have the expected bitmaps
        for vol, bitmaps in (
                [dst_base, base_bitmaps], [dst_top, top_bitmaps]):
            info = qemuimg.info(vol)
            assert info['format-specific']['data']['bitmaps'] == [
                {
                    "flags": ["auto"],
                    "name": bitmaps[0],
                    "granularity": 65536
                },
                {
                    "flags": ["auto"],
                    "name": bitmaps[1],
                    "granularity": 65536
                },
            ]

    def test_convert_without_copy_bitmaps(self, tmp_mount):
        virtual_size = MiB

        # Create source chain.
        src = os.path.join(tmp_mount.path, 'src.img')
        op = qemuimg.create(
            src,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1'
        )
        op.run()

        # Add new bitmap to src_base
        op = qemuimg.bitmap_add(src, 'bitmap')
        op.run()

        dst = os.path.join(tmp_mount.path, 'dst.img')
        op = qemuimg.create(
            dst,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1',
        )
        op.run()

        op = qemuimg.convert(
            src,
            dst,
            srcFormat=qemuimg.FORMAT.QCOW2,
            dstFormat=qemuimg.FORMAT.QCOW2,
            dstQcow2Compat='1.1',
        )
        op.run()

        # validate that bitmaps doesn't copied to
        # the leaf volume
        info = qemuimg.info(dst)
        assert 'bitmaps' not in info['format-specific']['data']

    def test_copy_with_disabled_bitmaps(self, tmp_mount):
        virtual_size = MiB
        bitmaps = [("a", True), ("b", False)]

        # Create source chain.
        src = os.path.join(tmp_mount.path, 'src.img')
        op = qemuimg.create(
            src,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1'
        )
        op.run()

        # Add bitmaps to src
        for name, enable in bitmaps:
            op = qemuimg.bitmap_add(src, name, enable=enable)
            op.run()

        dst = os.path.join(tmp_mount.path, 'dst.img')
        op = qemuimg.create(
            dst,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1',
        )
        op.run()

        op = qemuimg.convert(
            src,
            dst,
            srcFormat=qemuimg.FORMAT.QCOW2,
            dstFormat=qemuimg.FORMAT.QCOW2,
            dstQcow2Compat='1.1',
            bitmaps=True
        )
        op.run()

        # validate that bitmaps were copied to the
        # leaf volume including the invalid bitmap
        info = qemuimg.info(dst)
        assert info['format-specific']['data']['bitmaps'] == [
            {
                "flags": ["auto"],
                "name": "a",
                "granularity": 65536
            },
            {
                "flags": [],
                "name": "b",
                "granularity": 65536
            },
        ]

    def test_convert_with_inconsistent_bitmaps(self, tmp_mount):
        virtual_size = MiB
        broken_bitmap = 'broken_bitmap'

        # Create source volume.
        src_vol = os.path.join(tmp_mount.path, 'src_vol.img')
        op = qemuimg.create(
            src_vol,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1'
        )
        op.run()

        # Add new bitmaps to src_base
        qemuimg.bitmap_add(src_vol, broken_bitmap).run()

        # Simulate qemu crash, leaving bitmaps with the "in-use"
        # flag by opening the image for writing and killing the process.
        qemuio.abort(src_vol)

        # Create destination chain.
        dst_vol = os.path.join(tmp_mount.path, 'dst_vol.img')
        op = qemuimg.create(
            dst_vol,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1'
        )
        op.run()

        # Convert volume with inconsistent bitmaps when
        # QEMU support of '--skip-broken-bitmaps' flag
        # should skip the broken bitmaps -
        # https://bugzilla.redhat.com/1984852
        op = qemuimg.convert(
            src_vol,
            dst_vol,
            srcFormat=qemuimg.FORMAT.QCOW2,
            dstFormat=qemuimg.FORMAT.QCOW2,
            dstQcow2Compat='1.1',
            bitmaps=True
        )
        op.run()

        info = qemuimg.info(dst_vol)
        vol_bitmaps = info['format-specific']['data'].get("bitmaps", [])
        assert broken_bitmap not in vol_bitmaps


class TestConvertCompressed:

    def test_raw_to_compressed_qcow2(self, tmpdir):
        src_file = str(tmpdir.join("test.raw"))
        dst_file = str(tmpdir.join("test.qcow2"))
        with io.open(src_file, "wb") as f:
            f.truncate(1 * GiB)
            f.write(b"x" * MiB)

        src_file_size = qemuimg.info(src_file)["actual-size"]
        op = qemuimg.convert(
            src_file,
            dst_file,
            srcFormat=qemuimg.FORMAT.RAW,
            dstFormat=qemuimg.FORMAT.QCOW2,
            compressed=True)
        op.run()
        dst_file_size = qemuimg.info(dst_file)["actual-size"]

        assert src_file_size > dst_file_size

    def test_qcow2_to_compressed_qcow2(self, tmpdir):
        src_file = str(tmpdir.join("test_src.qcow2"))
        dst_file = str(tmpdir.join("test_dst.qcow2"))

        op = qemuimg.create(
            src_file,
            size=1 * GiB,
            format=qemuimg.FORMAT.QCOW2)
        op.run()
        qemuio.write_pattern(
            src_file,
            qemuimg.FORMAT.QCOW2,
            len=1 * MiB,
            pattern=0xf0)

        src_file_size = qemuimg.info(src_file)["actual-size"]
        op = qemuimg.convert(
            src_file,
            dst_file,
            srcFormat=qemuimg.FORMAT.QCOW2,
            dstFormat=qemuimg.FORMAT.QCOW2,
            compressed=True)
        op.run()
        dst_file_size = qemuimg.info(dst_file)["actual-size"]

        assert src_file_size > dst_file_size


class TestConvertUnorderedWrites:
    """
    Unordered writes are recommended only for raw format on block device, so we
    test only convert to raw.
    """

    @pytest.mark.parametrize("format", [
        qemuimg.FORMAT.RAW,
        qemuimg.FORMAT.QCOW2
    ])
    def test_single(self, tmpdir, format):
        src = str(tmpdir.join("src"))
        dst = str(tmpdir.join("dst"))
        offset = 4 * 64 * KiB

        op = qemuimg.create(
            src, size=10 * 64 * KiB, format=format, qcow2Compat="1.1")
        op.run()
        qemuio.write_pattern(src, format, offset=offset)

        op = qemuimg.convert(
            src,
            dst,
            srcFormat=format,
            dstFormat=qemuimg.FORMAT.RAW,
            unordered_writes=True)
        op.run()

        qemuio.verify_pattern(dst, qemuimg.FORMAT.RAW, offset=offset)

    def test_chain(self, tmpdir):
        base = str(tmpdir.join("base"))
        top = str(tmpdir.join("top"))
        dst = str(tmpdir.join("dst"))

        base_offset = 4 * 64 * KiB
        top_offset = 5 * 64 * KiB

        # Create base image with pattern.
        op = qemuimg.create(
            base, size=10 * 64 * KiB, format=qemuimg.FORMAT.RAW)
        op.run()
        qemuio.write_pattern(base, qemuimg.FORMAT.RAW, offset=base_offset)

        # Create top image with pattern.
        op = qemuimg.create(
            top,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat="1.1",
            backing=base,
            backingFormat=qemuimg.FORMAT.RAW)
        op.run()
        qemuio.write_pattern(top, qemuimg.FORMAT.QCOW2, offset=top_offset)

        # Convert, collpasing top and base into dst.
        op = qemuimg.convert(
            top,
            dst,
            srcFormat=qemuimg.FORMAT.QCOW2,
            dstFormat=qemuimg.FORMAT.RAW,
            unordered_writes=True)
        op.run()

        # Verify patterns
        qemuio.verify_pattern(dst, qemuimg.FORMAT.RAW, offset=base_offset)
        qemuio.verify_pattern(dst, qemuimg.FORMAT.RAW, offset=top_offset)


class TestConvertPreallocation:

    @pytest.mark.parametrize("preallocation", [
        qemuimg.PREALLOCATION.FALLOC,
        qemuimg.PREALLOCATION.FULL,
    ])
    def test_raw_to_raw_preallocation(self, preallocation):
        virtual_size = 10 * MiB
        with namedTemporaryDir() as tmpdir:
            src = os.path.join(tmpdir, 'src')
            dst = os.path.join(tmpdir, 'dst')

            with io.open(src, "wb") as f:
                f.truncate(virtual_size)

            op = qemuimg.convert(src, dst, srcFormat="raw", dstFormat="raw",
                                 preallocation=preallocation)
            op.run()
            check_raw_preallocated_image(dst, virtual_size)

    @pytest.mark.parametrize("preallocation", [
        None,
        qemuimg.PREALLOCATION.OFF
    ])
    def test_raw_to_raw_preallocation_off(self, preallocation):
        virtual_size = 10 * MiB
        with namedTemporaryDir() as tmpdir:
            src = os.path.join(tmpdir, 'src')
            dst = os.path.join(tmpdir, 'dst')

            with io.open(src, "wb") as f:
                f.truncate(virtual_size)

            op = qemuimg.convert(src, dst, srcFormat="raw", dstFormat="raw",
                                 preallocation=preallocation)
            op.run()
            check_raw_sparse_image(dst, virtual_size)

    @pytest.mark.parametrize("preallocation", [
        qemuimg.PREALLOCATION.FALLOC,
        qemuimg.PREALLOCATION.FULL,
    ])
    def test_qcow2_to_raw_preallocated(self, preallocation):
        virtual_size = 10 * MiB
        with namedTemporaryDir() as tmpdir:
            src = os.path.join(tmpdir, 'src')
            dst = os.path.join(tmpdir, 'dst')

            op = qemuimg.create(src, size=virtual_size, format="qcow2")
            op.run()

            op = qemuimg.convert(src, dst, srcFormat="qcow2", dstFormat="raw",
                                 preallocation=preallocation)
            op.run()
            check_raw_preallocated_image(dst, virtual_size)

    @pytest.mark.parametrize("preallocation", [
        None,
        qemuimg.PREALLOCATION.OFF
    ])
    def test_qcow2_to_raw_sparse(self, preallocation):
        virtual_size = 10 * MiB
        with namedTemporaryDir() as tmpdir:
            src = os.path.join(tmpdir, 'src')
            dst = os.path.join(tmpdir, 'dst')

            op = qemuimg.create(src, size=virtual_size, format="qcow2")
            op.run()

            op = qemuimg.convert(src, dst, srcFormat="qcow2", dstFormat="raw",
                                 preallocation=preallocation)
            op.run()
            check_raw_sparse_image(dst, virtual_size)

    def test_raw_invalid_preallocation(self):
        with pytest.raises(ValueError):
            qemuimg.convert(
                'src', 'dst', dstFormat="raw",
                preallocation=qemuimg.PREALLOCATION.METADATA)

    def test_raw_to_qcow2_metadata_prealloc(self):
        virtual_size = 10 * MiB
        with namedTemporaryDir() as tmpdir:
            src = os.path.join(tmpdir, 'src')
            dst = os.path.join(tmpdir, 'dst')

            op = qemuimg.create(src, size=virtual_size, format="raw")
            op.run()

            op = qemuimg.convert(src, dst, srcFormat="raw", dstFormat="qcow2",
                                 preallocation=qemuimg.PREALLOCATION.METADATA)
            op.run()

            actual_size = os.stat(dst).st_size
            disk_size = qemuimg.info(dst, format="qcow2")["actual-size"]

            assert actual_size > virtual_size
            assert disk_size < virtual_size


def check_raw_sparse_image(path, virtual_size):
    # Recent qemu-img always allocate the first block of an image;
    # older versions allocate nothing.
    # https://github.com/qemu/qemu/commit/3a20013fbb26
    image_stat = os.stat(path)
    allocated = image_stat.st_blocks * 512

    # On XFS filesystem qemu may set extent size of 1 MiB for better
    # performance.
    min_allocation = max(os.statvfs(path).f_bsize, MiB)

    assert image_stat.st_size == virtual_size
    assert allocated <= min_allocation


def check_raw_preallocated_image(path, virtual_size):
    image_stat = os.stat(path)
    assert image_stat.st_size == virtual_size
    assert image_stat.st_blocks * 512 == virtual_size


class TestCheck:

    @MonkeyPatch(qemuimg, 'config', CONFIG)
    def test_check(self):
        with namedTemporaryDir() as tmpdir:
            path = os.path.join(tmpdir, 'test.qcow2')
            op = qemuimg.create(path, size=MiB, format=qemuimg.FORMAT.QCOW2)
            op.run()
            info = qemuimg.check(path)
            # The exact value depends on qcow2 internals
            assert isinstance(info['offset'], int)

    def test_offset_no_match(self):
        with MonkeyPatchScope([(commands, "execCmd",
                                partial(fake_json_call, {}))]):
            with pytest.raises(cmdutils.Error):
                qemuimg.check('unused')

    def test_parse_error(self):
        def call(cmd, **kw):
            out = b"image: leaf.img\ninvalid file format line"
            return 0, out, ""

        with MonkeyPatchScope([(commands, "execCmd", call)]):
            with pytest.raises(cmdutils.Error):
                qemuimg.check('unused')


class TestProgressCommand:

    def test_failure(self):
        p = qemuimg.ProgressCommand(['false'])
        with pytest.raises(cmdutils.Error):
            p.run()

    def test_no_progress(self):
        p = qemuimg.ProgressCommand(['true'])
        p.run()
        assert p.progress == 0.0

    def test_progress(self):
        p = qemuimg.ProgressCommand([
            'echo', "-n",
            "    (0.00/100%)\r    (50.00/100%)\r    (100.00/100%)\r"
        ])
        p.run()
        assert p.progress == 100.0

    def test_partial_progress(self):
        p = qemuimg.ProgressCommand([])
        out = bytearray()
        out += b"    (42.00/100%)\r"
        p._update_progress(out)
        assert p.progress == 42.0
        assert out == b""
        out += b"    (43.00/"
        p._update_progress(out)
        assert p.progress == 42.0
        assert out == b"    (43.00/"
        out += b"100%)\r"
        p._update_progress(out)
        assert p.progress == 43.0
        assert out == b""

    def test_use_last_progress(self):
        p = qemuimg.ProgressCommand([])
        out = bytearray()
        out += b"    (11.00/100%)\r    (12.00/100%)\r    (13.00/100%)\r"
        p._update_progress(out)
        assert p.progress == 13.0
        assert out == b""

    def test_unexpected_output(self):
        p = qemuimg.ProgressCommand([])
        out = bytearray()
        out += b"    (42.00/100%)\r"
        p._update_progress(out)
        out += b"invalid progress\r"
        with pytest.raises(ValueError):
            p._update_progress(out)
        assert p.progress == 42.0


class TestCommit:

    @pytest.mark.parametrize("qcow2_compat", ["0.10", "1.1"])
    @pytest.mark.parametrize("base,top,use_base", [
        # Merging internal volume into its parent volume in raw format
        (0, 1, False),
        (0, 1, True),
        # Merging internal volume into its parent volume in cow format
        (1, 2, True),
        (1, 2, True),
        # Merging a subchain
        (1, 3, True),
        (1, 3, True),
        # Merging the entire chain into the base
        (0, 3, True),
        (0, 3, True),
    ])
    def test_commit(self, qcow2_compat, base, top, use_base):
        size = MiB
        with namedTemporaryDir() as tmpdir:
            chain = []
            parent = None
            parent_format = None

            # Create a chain of 4 volumes.
            for i in range(4):
                vol = os.path.join(tmpdir, "vol%d.img" % i)
                format = (qemuimg.FORMAT.RAW if i == 0 else
                          qemuimg.FORMAT.QCOW2)
                make_image(
                    vol,
                    size,
                    format,
                    i,
                    qcow2_compat,
                    backing=parent,
                    backing_format=parent_format)
                orig_offset = qemuimg.check(vol)["offset"] if i > 0 else None
                chain.append((vol, orig_offset))
                parent = vol
                parent_format = format

            base_vol = chain[base][0]
            top_vol = chain[top][0]
            op = qemuimg.commit(top_vol,
                                topFormat=qemuimg.FORMAT.QCOW2,
                                base=base_vol if use_base else None)
            op.run()

            base_fmt = (qemuimg.FORMAT.RAW if base == 0 else
                        qemuimg.FORMAT.QCOW2)
            for i in range(base, top + 1):
                offset = i * KiB
                pattern = 0xf0 + i
                # The base volume must have the data from all the volumes
                # merged into it.
                qemuio.verify_pattern(
                    base_vol,
                    base_fmt,
                    offset=offset,
                    len=KiB,
                    pattern=pattern)

                if i > base:
                    # internal and top volumes should keep the data, we
                    # may want to wipe this data when deleting the volumes
                    # later.
                    vol, orig_offset = chain[i]
                    actual_offset = qemuimg.check(vol)["offset"]
                    assert actual_offset == orig_offset

    def test_commit_progress(self):
        with namedTemporaryDir() as tmpdir:
            size = MiB
            base = os.path.join(tmpdir, "base.img")
            make_image(base, size, qemuimg.FORMAT.RAW, 0, "1.1")

            top = os.path.join(tmpdir, "top.img")
            make_image(
                top,
                size,
                qemuimg.FORMAT.QCOW2,
                1,
                "1.1",
                backing=base,
                backing_format=qemuimg.FORMAT.RAW)

            op = qemuimg.commit(top, topFormat=qemuimg.FORMAT.QCOW2)
            op.run()
            assert 100 == op.progress


class TestMap:

    # We test only qcow2 images since this is the only use case that we need
    # now.  Testing raw images is tricky, the result depends on the file system
    # supporting SEEK_DATA and SEEK_HOLE. If these are supported, empty image
    # will be seen as one block with data=False. If not supported (seen on
    # travis-ci), empty image will be seen as one block with data=True.
    FORMAT = qemuimg.FORMAT.QCOW2

    @pytest.mark.parametrize("qcow2_compat", ["0.10", "1.1"])
    def test_empty_image(self, qcow2_compat):
        with namedTemporaryDir() as tmpdir:
            size = MiB
            image = os.path.join(tmpdir, "base.img")
            op = qemuimg.create(image, size=size, format=self.FORMAT,
                                qcow2Compat=qcow2_compat)
            op.run()

            expected = [
                # single run - empty
                {
                    "start": 0,
                    "length": size,
                    "data": False,
                    "zero": True,
                },
            ]

            self.check_map(qemuimg.map(image), expected)

    @pytest.mark.parametrize("qcow2_compat", ["0.10", "1.1"])
    def test_one_cluster(self, qcow2_compat):
        with namedTemporaryDir() as tmpdir:
            # Write full clusters so we dont fail when qemu change the
            # implemention of writing partial cluster.
            offset = 64 * KiB
            length = 64 * KiB
            size = MiB

            image = os.path.join(tmpdir, "base.img")
            op = qemuimg.create(image, size=size, format=self.FORMAT,
                                qcow2Compat=qcow2_compat)
            op.run()

            qemuio.write_pattern(
                image,
                self.FORMAT,
                offset=offset,
                len=length,
                pattern=0xf0)

            expected = [
                # run 1 - empty
                {
                    "start": 0,
                    "length": length,
                    "data": False,
                    "zero": True,
                },
                # run 2 - data
                {
                    "start": offset,
                    "length": length,
                    "data": True,
                    "zero": False,
                },
                # run 3 - empty
                {
                    "start": offset + length,
                    "length": size - offset - length,
                    "data": False,
                    "zero": True,
                },
            ]

            self.check_map(qemuimg.map(image), expected)

    def check_map(self, actual, expected):
        if len(expected) != len(actual):
            msg = "Length mismatch: %d != %d" % (len(expected), len(actual))
            raise MapMismatch(msg, expected, actual)

        for actual_run, expected_run in zip(actual, expected):
            for key in expected_run:
                if expected_run[key] != actual_run[key]:
                    msg = "Value mismatch for %r: %s != %s" % (
                        key, expected_run, actual_run)
                    raise MapMismatch(msg, expected, actual)


class TestAmend:

    @pytest.mark.parametrize("qcow2_compat,desired_compat", [
        ("0.10", "1.1"),
        ("0.10", "0.10"),
        ("1.1", "1.1"),
    ])
    def test_empty_image(self, monkeypatch, qcow2_compat, desired_compat):
        monkeypatch.setattr(qemuimg, 'config', CONFIG)
        with namedTemporaryDir() as tmpdir:
            base_path = os.path.join(tmpdir, 'base.img')
            leaf_path = os.path.join(tmpdir, 'leaf.img')
            size = MiB
            op_base = qemuimg.create(
                base_path,
                size=size,
                format=qemuimg.FORMAT.RAW)
            op_base.run()
            op_leaf = qemuimg.create(
                leaf_path,
                format=qemuimg.FORMAT.QCOW2,
                backing=base_path,
                backingFormat=qemuimg.FORMAT.RAW)
            op_leaf.run()
            qemuimg.amend(leaf_path, desired_compat)
            info = qemuimg.info(leaf_path)
            assert info['format-specific']['data']['compat'] == desired_compat


Chain = namedtuple("Chain", "top,base,parent")


@pytest.fixture(params=['0.10', '1.1'])
def file_chain(tmpdir, request):
    compat = request.param
    size = 1 * GiB

    # Create base parent volume
    parent = str(tmpdir.join("parent"))
    op = qemuimg.create(
        parent,
        size=size,
        format=qemuimg.FORMAT.RAW,
        qcow2Compat=compat)
    op.run()

    # Write 1 MiB to parent
    qemuio.write_pattern(
        parent,
        format=qemuimg.FORMAT.RAW,
        offset=0 * MiB,
        len=1 * MiB,
        pattern=0xf0)

    # Create base volume over parent
    base = str(tmpdir.join("base"))
    op = qemuimg.create(
        base,
        size=size,
        format=qemuimg.FORMAT.QCOW2,
        qcow2Compat=compat,
        backing=parent,
        backingFormat=qemuimg.FORMAT.RAW)
    op.run()

    # Write 1 MiB to base
    qemuio.write_pattern(
        base,
        format=qemuimg.FORMAT.QCOW2,
        offset=1 * MiB,
        len=1 * MiB,
        pattern=0xf1)

    # Create top volume over base
    top = str(tmpdir.join("top"))
    op = qemuimg.create(
        top,
        size=size,
        format=qemuimg.FORMAT.QCOW2,
        qcow2Compat=compat,
        backing=base,
        backingFormat=qemuimg.FORMAT.QCOW2)
    op.run()

    # Write 1 MiB to top
    qemuio.write_pattern(
        top,
        format=qemuimg.FORMAT.QCOW2,
        offset=2 * MiB,
        len=1 * MiB,
        pattern=0xf2)

    return Chain(top, base, parent)


@pytest.fixture(params=['0.10', '1.1'])
def block_chain(tmp_storage, request):
    compat = request.param
    dev_size = 1 * GiB

    # Create raw parent volume
    parent = tmp_storage.create_device(dev_size)
    op = qemuimg.create(
        parent,
        size=dev_size,
        format=qemuimg.FORMAT.RAW)
    op.run()

    # Create base volume over parent
    base = tmp_storage.create_device(dev_size)
    op = qemuimg.create(
        base,
        size=dev_size,
        format=qemuimg.FORMAT.QCOW2,
        qcow2Compat=compat,
        backing=parent,
        backingFormat=qemuimg.FORMAT.RAW)
    op.run()

    # Write 1 MiB to base
    qemuio.write_pattern(
        base,
        format=qemuimg.FORMAT.QCOW2,
        offset=1 * MiB,
        len=1 * MiB,
        pattern=0xf1)

    # Create top volume over base
    top = tmp_storage.create_device(dev_size)
    op = qemuimg.create(
        top,
        size=dev_size,
        format=qemuimg.FORMAT.QCOW2,
        qcow2Compat=compat,
        backing=base,
        backingFormat=qemuimg.FORMAT.QCOW2)
    op.run()

    # Write 1 MiB to top
    qemuio.write_pattern(
        top,
        format=qemuimg.FORMAT.QCOW2,
        offset=2 * MiB,
        len=1 * MiB,
        pattern=0xf2)

    return Chain(top, base, parent)


class TestMeasure:

    @pytest.mark.parametrize("format,compressed", [
        (qemuimg.FORMAT.RAW, False),
        (qemuimg.FORMAT.QCOW2, False),
        (qemuimg.FORMAT.QCOW2, True),
    ])
    @pytest.mark.parametrize("compat", ['0.10', '1.1'])
    @pytest.mark.parametrize("size", [1, 100])
    def test_empty(self, tmpdir, compat, size, format, compressed):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.truncate(size * GiB)
        self.check_measure(filename, compat, format, compressed)

    @pytest.mark.parametrize("format,compressed", [
        (qemuimg.FORMAT.RAW, False),
        (qemuimg.FORMAT.QCOW2, False),
        (qemuimg.FORMAT.QCOW2, True),
    ])
    @pytest.mark.parametrize("compat", ['0.10', '1.1'])
    @pytest.mark.parametrize("size", [1, 100])
    def test_best_small(self, tmpdir, compat, size, format, compressed):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.truncate(size * GiB)
            f.write(b"x" * MiB)
        self.check_measure(filename, compat, format, compressed)

    @pytest.mark.parametrize("format,compressed", [
        (qemuimg.FORMAT.RAW, False),
        (qemuimg.FORMAT.QCOW2, False),
        (qemuimg.FORMAT.QCOW2, True),
    ])
    @pytest.mark.parametrize("compat", ['0.10', '1.1'])
    @pytest.mark.parametrize("size", [1, 100])
    def test_big(self, tmpdir, compat, size, format, compressed):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.truncate(size * GiB)
            f.write(b"x" * MiB)
            f.seek(512 * MiB)
            f.write(b"x" * MiB)
        self.check_measure(filename, compat, format, compressed)

    @pytest.mark.slow
    @pytest.mark.parametrize("format,compressed", [
        (qemuimg.FORMAT.RAW, False),
        (qemuimg.FORMAT.QCOW2, False),
        (qemuimg.FORMAT.QCOW2, True),
    ])
    @pytest.mark.parametrize("compat", ['0.10', '1.1'])
    def test_worst(self, tmpdir, compat, format, compressed):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.truncate(1 * GiB)
            for off in range(0, GiB, CLUSTER_SIZE):
                f.seek(off)
        self.check_measure(filename, compat, format, compressed)

    @pytest.mark.slow
    @pytest.mark.parametrize("format,compressed", [
        (qemuimg.FORMAT.RAW, False),
        (qemuimg.FORMAT.QCOW2, False),
        (qemuimg.FORMAT.QCOW2, True),
    ])
    @pytest.mark.parametrize("compat", ['0.10', '1.1'])
    def test_full(self, tmpdir, compat, format, compressed):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.truncate(1 * GiB)
            for _ in range(1024):
                f.write(b"x" * MiB)
        self.check_measure(filename, compat, format, compressed)

    @requires_root
    @pytest.mark.root
    def test_block(self, block_chain):
        # Creating a block chain is very slow, so we reuse it for many tests.

        top = qemuimg.measure(
            block_chain.top,
            format=qemuimg.FORMAT.QCOW2,
            output_format=qemuimg.FORMAT.QCOW2,
            is_block=True)

        base = qemuimg.measure(
            block_chain.base,
            format=qemuimg.FORMAT.QCOW2,
            output_format=qemuimg.FORMAT.QCOW2,
            is_block=True)

        parent = qemuimg.measure(
            block_chain.parent,
            format=qemuimg.FORMAT.RAW,
            output_format=qemuimg.FORMAT.QCOW2,
            is_block=True)

        # Becasue parent is raw, measuring any layer including the backing
        # chain reports fully allocated value.
        assert top["required"] == base["required"] == parent["required"]
        assert GiB < top["required"] < GiB + 0.5 * MiB

        top_only = qemuimg.measure(
            block_chain.top,
            format=qemuimg.FORMAT.QCOW2,
            output_format=qemuimg.FORMAT.QCOW2,
            is_block=True,
            backing=False)

        assert MiB < top_only["required"] < 1.5 * MiB

        base_only = qemuimg.measure(
            block_chain.base,
            format=qemuimg.FORMAT.QCOW2,
            output_format=qemuimg.FORMAT.QCOW2,
            is_block=True,
            backing=False)

        assert top_only == base_only

        sub_chain = qemuimg.measure(
            block_chain.top,
            format=qemuimg.FORMAT.QCOW2,
            output_format=qemuimg.FORMAT.QCOW2,
            is_block=True,
            base=block_chain.base)

        assert 2 * MiB < sub_chain["required"] < 2.5 * MiB

        with pytest.raises(ValueError) as e:
            qemuimg.measure(
                block_chain.top,
                format=qemuimg.FORMAT.QCOW2,
                output_format=qemuimg.FORMAT.QCOW2,
                base=block_chain.parent)

        # Check that we include the invalid and actual base in the error.
        assert block_chain.parent in str(e.value)
        assert block_chain.base in str(e.value)

    def test_file_base(self, file_chain):
        entire_chain = qemuimg.measure(
            file_chain.top,
            format=qemuimg.FORMAT.QCOW2,
            output_format=qemuimg.FORMAT.QCOW2)

        # parent <- [base <- top]
        sub_chain1 = qemuimg.measure(
            file_chain.top,
            format=qemuimg.FORMAT.QCOW2,
            output_format=qemuimg.FORMAT.QCOW2,
            base=file_chain.base)

        assert entire_chain["required"] >= sub_chain1["required"] + MiB

        # [parent <- base] <- top
        sub_chain2 = qemuimg.measure(
            file_chain.base,
            format=qemuimg.FORMAT.QCOW2,
            output_format=qemuimg.FORMAT.QCOW2,
            base=file_chain.parent)

        assert sub_chain1 == sub_chain2

    def test_file_base_invalid(self, file_chain):
        # Trying to measure unsupported chain:
        # [parent <- base <- top]
        with pytest.raises(ValueError) as e:
            qemuimg.measure(
                file_chain.top,
                format=qemuimg.FORMAT.QCOW2,
                output_format=qemuimg.FORMAT.QCOW2,
                base=file_chain.parent)

        # Check that we include the invalid and actual base in the error.
        assert file_chain.parent in str(e.value)
        assert file_chain.base in str(e.value)

    def test_file_leaf(self, file_chain):
        entire_chain = qemuimg.measure(
            file_chain.top,
            format=qemuimg.FORMAT.QCOW2,
            output_format=qemuimg.FORMAT.QCOW2)

        top_only = qemuimg.measure(
            file_chain.top,
            format=qemuimg.FORMAT.QCOW2,
            output_format=qemuimg.FORMAT.QCOW2,
            backing=False)

        assert entire_chain["required"] >= top_only["required"] + 2 * MiB

        base_only = qemuimg.measure(
            file_chain.base,
            format=qemuimg.FORMAT.QCOW2,
            output_format=qemuimg.FORMAT.QCOW2,
            backing=False)

        assert top_only == base_only

    def check_measure(self, filename, compat, format, compressed):
        if format != qemuimg.FORMAT.RAW:
            filename = convert_to_qcow2(filename, compressed=compressed,
                                        compat=compat)
        qemu_info = qemuimg.info(filename)
        virtual_size = qemu_info["virtual-size"]
        qemu_measure = qemuimg.measure(
            filename,
            format=format,
            output_format=qemuimg.FORMAT.QCOW2)
        estimated_size = qemu_measure["required"]
        actual_size = converted_size(filename, compat=compat)
        error_pct = 100 * float(estimated_size - actual_size) / virtual_size
        assert estimated_size >= actual_size
        assert error_pct <= 0.1, error_pct

    def test_active_image_fail(self, file_chain):
        # Measuring an active image without unsafe=True fails.
        with qemuio.open(file_chain.top, qemuimg.FORMAT.QCOW2):
            with pytest.raises(cmdutils.Error):
                qemuimg.measure(
                    file_chain.top,
                    format=qemuimg.FORMAT.QCOW2,
                    output_format=qemuimg.FORMAT.QCOW2)

    def test_active_image_unsafe(self, file_chain):
        # We can measure an active image with unsafe=True.
        with qemuio.open(file_chain.top, qemuimg.FORMAT.QCOW2):
            active = qemuimg.measure(
                file_chain.top,
                format=qemuimg.FORMAT.QCOW2,
                output_format=qemuimg.FORMAT.QCOW2,
                unsafe=True)

        inactive = qemuimg.measure(
            file_chain.top,
            format=qemuimg.FORMAT.QCOW2,
            output_format=qemuimg.FORMAT.QCOW2)

        assert active == inactive


class TestBitmaps:

    @pytest.mark.parametrize("granularity, exp_granularity", [
        (None, 65536),
        (8 * 64 * 1024, 8 * 64 * 1024)
    ])
    def test_add_remove_bitmap(
            self, tmp_mount, granularity, exp_granularity):
        virtual_size = MiB
        bitmap_name = 'bitmap1'
        # Create source file
        src_path = os.path.join(tmp_mount.path, 'source.img')
        op = qemuimg.create(
            src_path,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1'
        )
        op.run()

        # Add new bitmap to src
        op = qemuimg.bitmap_add(
            src_path,
            bitmap_name,
            granularity=granularity
        )
        op.run()

        info = qemuimg.info(src_path)
        assert info['format-specific']['data']['bitmaps'] == [
            {
                "flags": ["auto"],
                "name": bitmap_name,
                "granularity": exp_granularity
            },
        ]

        # Remove bitmap from src
        op = qemuimg.bitmap_remove(src_path, bitmap_name)
        op.run()

        info = qemuimg.info(src_path)
        assert 'bitmaps' not in info['format-specific']['data']

    def test_add_disabled_bitmap(self, tmp_mount):
        virtual_size = MiB
        bitmap_name = 'bitmap1'
        # Create source file
        src_path = os.path.join(tmp_mount.path, 'source.img')
        op = qemuimg.create(
            src_path,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1'
        )
        op.run()

        # Add new disabled bitmap to src
        op = qemuimg.bitmap_add(
            src_path,
            bitmap_name,
            enable=False
        )
        op.run()

        info = qemuimg.info(src_path)
        assert info['format-specific']['data']['bitmaps'] == [
            {
                "flags": [],
                "name": bitmap_name,
                "granularity": 65536
            },
        ]

    def test_enable_disable_bitmap(self, tmp_mount):
        virtual_size = MiB
        bitmap_name = 'bitmap1'
        # Create source file
        src_path = os.path.join(tmp_mount.path, 'source.img')
        op = qemuimg.create(
            src_path,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1'
        )
        op.run()

        # Add new bitmap to src
        op = qemuimg.bitmap_add(
            src_path,
            bitmap_name,
        )
        op.run()

        # Disable the bitmap on src
        op = qemuimg.bitmap_update(
            src_path,
            bitmap_name,
            enable=False
        )
        op.run()

        info = qemuimg.info(src_path)
        assert info['format-specific']['data']['bitmaps'] == [
            {
                "flags": [],
                "name": bitmap_name,
                "granularity": 65536
            },
        ]

        # Enable the bitmap on src
        op = qemuimg.bitmap_update(
            src_path,
            bitmap_name,
            enable=True
        )
        op.run()

        info = qemuimg.info(src_path)
        assert info['format-specific']['data']['bitmaps'] == [
            {
                "flags": ["auto"],
                "name": bitmap_name,
                "granularity": 65536
            },
        ]

    def test_merge_bitmaps(self, tmp_mount):
        virtual_size = MiB
        base_bitmap = 'base_bitmap'
        top_bitmap = 'top_bitmap'

        # Create base file
        base_path = os.path.join(tmp_mount.path, 'base.img')
        op = qemuimg.create(
            base_path,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1'
        )
        op.run()

        # Add new bitmap to base
        op = qemuimg.bitmap_add(
            base_path,
            base_bitmap,
        )
        op.run()

        # Create top file
        top_path = os.path.join(tmp_mount.path, 'src_top.img')
        op = qemuimg.create(
            top_path,
            size=virtual_size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1',
            backing=base_path,
            backingFormat='qcow2'
        )
        op.run()

        # Add new bitmap to top
        op = qemuimg.bitmap_add(
            top_path,
            top_bitmap,
        )
        op.run()

        # Merge bitmaps from top to base
        op = qemuimg.bitmap_merge(
            top_path,
            top_bitmap,
            'qcow2',
            base_path,
            base_bitmap
        )
        op.run()

        # TODO: This assert does not test the merge data
        # itself, only the bitmaps metadata.
        # We need to find a good way to test the
        # bitmap internals.
        info = qemuimg.info(base_path)
        assert info['format-specific']['data']['bitmaps'] == [
            {
                "flags": ["auto"],
                "name": base_bitmap,
                "granularity": 65536
            },
        ]


def converted_size(filename, compat):
    converted = convert_to_qcow2(filename, compat=compat)
    return os.stat(converted).st_size


def convert_to_qcow2(src, compressed=False, compat="1.1"):
    dst = src + ".qcow2"
    convert_cmd = qemuimg.convert(
        src,
        dst,
        dstFormat=qemuimg.FORMAT.QCOW2,
        dstQcow2Compat=compat,
        compressed=compressed)
    convert_cmd.run()
    os.remove(src)
    return dst


def make_image(
        path, size, format, index, qcow2_compat, backing=None,
        backing_format=None):
    op = qemuimg.create(
        path,
        size=size,
        format=format,
        qcow2Compat=qcow2_compat,
        backing=backing,
        backingFormat=backing_format)
    op.run()

    offset = index * KiB
    qemuio.write_pattern(
        path,
        format,
        offset=offset,
        len=KiB,
        pattern=0xf0 + index)


class MapMismatch(AssertionError):

    def __init__(self, message, expected, actual):
        self.msg = message
        self.expected = expected
        self.actual = actual

    def __str__(self):
        text = self.msg + "\n"
        text += "\n"
        text += "Expected map:\n"
        text += pprint.pformat(self.expected) + "\n"
        text += "\n"
        text += "Actual map:\n"
        text += pprint.pformat(self.actual) + "\n"
        return text
