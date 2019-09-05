#
# Copyright 2014-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import io
import json
import os
import pprint
from functools import partial

import pytest

from monkeypatch import MonkeyPatch, MonkeyPatchScope

from . import qemuio

from testlib import make_config
from testlib import namedTemporaryDir
from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common import constants
from testlib import temporaryPath
from vdsm.common import exception
from vdsm.common.constants import GIB
from vdsm.common.constants import MEGAB
from vdsm.storage import qemuimg

CLUSTER_SIZE = 64 * 1024

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
            "virtual-size": 1048576,
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
            "dirty-flag": False
        }

    def test_info(self):
        with namedTemporaryDir() as tmpdir:
            base_path = os.path.join(tmpdir, 'base.img')
            leaf_path = os.path.join(tmpdir, 'leaf.img')
            size = 1048576
            leaf_fmt = qemuimg.FORMAT.QCOW2
            with MonkeyPatchScope([(qemuimg, 'config', CONFIG)]):
                op = qemuimg.create(base_path,
                                    size=size,
                                    format=qemuimg.FORMAT.RAW)
                op.run()
                op = qemuimg.create(leaf_path,
                                    format=leaf_fmt,
                                    backing=base_path)
                op.run()

            info = qemuimg.info(leaf_path)
            assert leaf_fmt == info['format']
            assert size == info['virtualsize']
            assert self.CLUSTER_SIZE == info['clustersize']
            assert base_path == info['backingfile']
            assert '0.10' == info['compat']

    @pytest.mark.parametrize("unsafe", [True, False])
    def test_unsafe_info(self, unsafe):
        with namedTemporaryDir() as tmpdir:
            img = os.path.join(tmpdir, 'img.img')
            size = 1048576
            op = qemuimg.create(img, size=size, format=qemuimg.FORMAT.QCOW2)
            op.run()
            info = qemuimg.info(img, unsafe=unsafe)
            assert size == info['virtualsize']

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

    def test_missing_compat_for_qcow2_raises(self):
        data = self._fake_info()
        del data['format-specific']['data']['compat']
        with MonkeyPatchScope([(commands, "execCmd",
                                partial(fake_json_call, data))]):
            with pytest.raises(cmdutils.Error):
                qemuimg.info('leaf.img')

    @pytest.mark.parametrize("qemu_field,info_field", [
        ('backing-filename', 'backingfile'),
        ('cluster-size', 'clustersize'),
    ])
    def test_optional_fields(self, qemu_field, info_field):
        data = self._fake_info()
        del data[qemu_field]
        with MonkeyPatchScope([(commands, "execCmd",
                                partial(fake_json_call, data))]):
            info = qemuimg.info('unused')
            assert info_field not in info

    def test_compat_reported_for_qcow2_only(self):
        data = {
            "virtual-size": 1048576,
            "filename": "raw.img",
            "format": "raw",
            "actual-size": 0,
            "dirty-flag": False
        }
        with MonkeyPatchScope([(commands, "execCmd",
                                partial(fake_json_call, data))]):
            info = qemuimg.info('unused')
            assert 'compat' not in info

    def test_untrusted_image(self):
        with namedTemporaryDir() as tmpdir:
            img = os.path.join(tmpdir, 'untrusted.img')
            size = 500 * 1024**3
            op = qemuimg.create(img, size=size, format=qemuimg.FORMAT.QCOW2)
            op.run()
            info = qemuimg.info(img, trusted_image=False)
            assert size == info['virtualsize']

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


class TestCreate:

    @pytest.mark.parametrize("preallocation", [
        qemuimg.PREALLOCATION.FALLOC,
        qemuimg.PREALLOCATION.FULL,
    ])
    def test_preallocation(self, preallocation):
        virtual_size = 10 * 1024**2
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
        virtual_size = 10 * 1024**2
        with temporaryPath() as image:
            op = qemuimg.create(
                image,
                size=virtual_size,
                format=qemuimg.FORMAT.RAW,
                preallocation=preallocation)
            op.run()
            check_raw_sparse_image(image, virtual_size)

    def test_no_format(self):
        size = 4096
        with namedTemporaryDir() as tmpdir:
            image = os.path.join(tmpdir, "image")
            op = qemuimg.create(image, size=size)
            op.run()

            info = qemuimg.info(image)
            assert info['format'] == qemuimg.FORMAT.RAW
            assert info['virtualsize'] == size

    def test_zero_size(self):
        with namedTemporaryDir() as tmpdir:
            image = os.path.join(tmpdir, "image")
            op = qemuimg.create(image, size=0)
            op.run()

            info = qemuimg.info(image)
            assert info['format'] == qemuimg.FORMAT.RAW
            assert info['virtualsize'] == 0

    def test_qcow2_compat(self):
        with namedTemporaryDir() as tmpdir:
            image = os.path.join(tmpdir, "image")
            size = 1024 * 1024 * 1024 * 10  # 10 GB
            op = qemuimg.create(image, format='qcow2', size=size)
            op.run()

            info = qemuimg.info(image)
            assert info['format'] == qemuimg.FORMAT.QCOW2
            assert info['compat'] == "0.10"
            assert info['virtualsize'] == size

    def test_qcow2_compat_version3(self):
        with namedTemporaryDir() as tmpdir:
            image = os.path.join(tmpdir, "image")
            size = 1024 * 1024 * 1024 * 10  # 10 GB
            op = qemuimg.create(image, format='qcow2',
                                qcow2Compat='1.1', size=size)
            op.run()

            info = qemuimg.info(image)
            assert info['format'] == qemuimg.FORMAT.QCOW2
            assert info['compat'] == "1.1"
            assert info['virtualsize'] == size

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
            qemuimg.create(path, size=1048576, format=qemuimg.FORMAT.QCOW2,
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
                        'src', '-O', 'qcow2', '-o', 'compat=0.10', 'dst']
            assert cmd == expected

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'ProgressCommand', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2')

    def test_qcow2_compat_version3(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        'src', '-O', 'qcow2', '-o', 'compat=1.1', 'dst']
            assert cmd == expected

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'ProgressCommand', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2',
                            dstQcow2Compat='1.1')

    def test_qcow2_no_backing_file(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        'src', '-O', 'qcow2', '-o', 'compat=0.10', 'dst']
            assert cmd == expected

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'ProgressCommand', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2')

    def test_qcow2_backing_file(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        'src', '-O', 'qcow2', '-o',
                        'compat=0.10,backing_file=bak', 'dst']
            assert cmd == expected

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'ProgressCommand', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2',
                            backing='bak')

    def test_qcow2_backing_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        'src', '-O', 'qcow2', '-o', 'compat=0.10', 'dst']
            assert cmd == expected

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'ProgressCommand', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2',
                            backingFormat='qcow2')

    def test_qcow2_backing_file_and_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        'src', '-O', 'qcow2', '-o',
                        'compat=0.10,backing_file=bak,backing_fmt=qcow2',
                        'dst']
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


class TestConvertCompressed:

    def test_raw_to_compressed_qcow2(self, tmpdir):
        src_file = str(tmpdir.join("test.raw"))
        dst_file = str(tmpdir.join("test.qcow2"))
        with io.open(src_file, "wb") as f:
            f.truncate(1 * GIB)
            f.write(b"x" * MEGAB)

        src_file_size = qemuimg.info(src_file)["actualsize"]
        op = qemuimg.convert(
            src_file,
            dst_file,
            srcFormat=qemuimg.FORMAT.RAW,
            dstFormat=qemuimg.FORMAT.QCOW2,
            compressed=True)
        op.run()
        dst_file_size = qemuimg.info(dst_file)["actualsize"]

        assert src_file_size > dst_file_size

    def test_qcow2_to_compressed_qcow2(self, tmpdir):
        src_file = str(tmpdir.join("test_src.qcow2"))
        dst_file = str(tmpdir.join("test_dst.qcow2"))

        op = qemuimg.create(
            src_file,
            size=1 * GIB,
            format=qemuimg.FORMAT.QCOW2)
        op.run()
        qemuio.write_pattern(
            src_file,
            qemuimg.FORMAT.QCOW2,
            len=1 * MEGAB,
            pattern=0xf0)

        src_file_size = qemuimg.info(src_file)["actualsize"]
        op = qemuimg.convert(
            src_file,
            dst_file,
            srcFormat=qemuimg.FORMAT.QCOW2,
            dstFormat=qemuimg.FORMAT.QCOW2,
            compressed=True)
        op.run()
        dst_file_size = qemuimg.info(dst_file)["actualsize"]

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
        offset = 4 * 64 * 1024

        op = qemuimg.create(
            src, size=10 * 64 * 1024, format=format, qcow2Compat="1.1")
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

        base_offset = 4 * 64 * 1024
        top_offset = 5 * 64 * 1024

        # Create base image with pattern.
        op = qemuimg.create(
            base, size=10 * 64 * 1024, format=qemuimg.FORMAT.RAW)
        op.run()
        qemuio.write_pattern(base, qemuimg.FORMAT.RAW, offset=base_offset)

        # Create top image with pattern.
        op = qemuimg.create(
            top, format=qemuimg.FORMAT.QCOW2, qcow2Compat="1.1", backing=base)
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
        virtual_size = 10 * 1024**2
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
        virtual_size = 10 * 1024**2
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
        virtual_size = 10 * 1024**2
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
        virtual_size = 10 * 1024**2
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
        virtual_size = 10 * 1024**2
        with namedTemporaryDir() as tmpdir:
            src = os.path.join(tmpdir, 'src')
            dst = os.path.join(tmpdir, 'dst')

            op = qemuimg.create(src, size=virtual_size, format="raw")
            op.run()

            op = qemuimg.convert(src, dst, srcFormat="raw", dstFormat="qcow2",
                                 preallocation=qemuimg.PREALLOCATION.METADATA)
            op.run()

            actual_size = os.stat(dst).st_size
            disk_size = qemuimg.info(dst, format="qcow2")["actualsize"]

            assert actual_size > virtual_size
            assert disk_size < virtual_size


def check_raw_sparse_image(path, virtual_size):
    # Recent qemu-img always allocate the first block of an image;
    # older versions allocate nothing.
    # https://github.com/qemu/qemu/commit/3a20013fbb26
    image_stat = os.stat(path)
    allocated = image_stat.st_blocks * 512
    filesystem_block_size = os.statvfs(path).f_bsize
    assert image_stat.st_size == virtual_size
    assert allocated <= filesystem_block_size


def check_raw_preallocated_image(path, virtual_size):
    image_stat = os.stat(path)
    assert image_stat.st_size == virtual_size
    assert image_stat.st_blocks * 512 == virtual_size


class TestCheck:

    @MonkeyPatch(qemuimg, 'config', CONFIG)
    def test_check(self):
        with namedTemporaryDir() as tmpdir:
            path = os.path.join(tmpdir, 'test.qcow2')
            op = qemuimg.create(path,
                                size=1048576,
                                format=qemuimg.FORMAT.QCOW2)
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
        size = 1048576
        with namedTemporaryDir() as tmpdir:
            chain = []
            parent = None
            # Create a chain of 4 volumes.
            for i in range(4):
                vol = os.path.join(tmpdir, "vol%d.img" % i)
                format = (qemuimg.FORMAT.RAW if i == 0 else
                          qemuimg.FORMAT.QCOW2)
                make_image(vol, size, format, i, qcow2_compat, parent)
                orig_offset = qemuimg.check(vol)["offset"] if i > 0 else None
                chain.append((vol, orig_offset))
                parent = vol

            base_vol = chain[base][0]
            top_vol = chain[top][0]
            op = qemuimg.commit(top_vol,
                                topFormat=qemuimg.FORMAT.QCOW2,
                                base=base_vol if use_base else None)
            op.run()

            base_fmt = (qemuimg.FORMAT.RAW if base == 0 else
                        qemuimg.FORMAT.QCOW2)
            for i in range(base, top + 1):
                offset = i * 1024
                pattern = 0xf0 + i
                # The base volume must have the data from all the volumes
                # merged into it.
                qemuio.verify_pattern(
                    base_vol,
                    base_fmt,
                    offset=offset,
                    len=1024,
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
            size = 1048576
            base = os.path.join(tmpdir, "base.img")
            make_image(base, size, qemuimg.FORMAT.RAW, 0, "1.1")

            top = os.path.join(tmpdir, "top.img")
            make_image(top, size, qemuimg.FORMAT.QCOW2, 1, "1.1", base)

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
            size = 1048576
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
    @pytest.mark.parametrize("offset,length,expected_length", [
        (64 * 1024, 4 * 1024, 65536),
        (64 * 1024, 72 * 1024, 131072),
    ])
    def test_one_block(self, offset, length, expected_length, qcow2_compat):
        with namedTemporaryDir() as tmpdir:
            size = 1048576
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
                    "length": offset,
                    "data": False,
                    "zero": True,
                },
                # run 2 - data
                {
                    "start": offset,
                    "length": expected_length,
                    "data": True,
                    "zero": False,
                },
                # run 3 - empty
                {
                    "start": offset + expected_length,
                    "length": size - offset - expected_length,
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
            size = 1048576
            op_base = qemuimg.create(base_path, size=size,
                                     format=qemuimg.FORMAT.RAW)
            op_base.run()
            op_leaf = qemuimg.create(leaf_path, format=qemuimg.FORMAT.QCOW2,
                                     backing=base_path)
            op_leaf.run()
            qemuimg.amend(leaf_path, desired_compat)
            assert qemuimg.info(leaf_path)['compat'] == desired_compat


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
            f.truncate(size * GIB)
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
            f.truncate(size * GIB)
            f.write(b"x" * MEGAB)
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
            f.truncate(size * GIB)
            f.write(b"x" * MEGAB)
            f.seek(512 * MEGAB)
            f.write(b"x" * MEGAB)
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
            f.truncate(1 * GIB)
            for off in range(0, GIB, CLUSTER_SIZE):
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
            f.truncate(1 * GIB)
            for _ in range(1024):
                f.write(b"x" * MEGAB)
        self.check_measure(filename, compat, format, compressed)

    def check_measure(self, filename, compat, format, compressed):
        if format != qemuimg.FORMAT.RAW:
            filename = convert_to_qcow2(filename, compressed=compressed,
                                        compat=compat)
        qemu_info = qemuimg.info(filename)
        virtual_size = qemu_info["virtualsize"]
        qemu_measure = qemuimg.measure(
            filename,
            format=format,
            output_format=qemuimg.FORMAT.QCOW2)
        estimated_size = qemu_measure["required"]
        actual_size = converted_size(filename, compat=compat)
        error_pct = 100 * float(estimated_size - actual_size) / virtual_size
        assert estimated_size >= actual_size
        assert error_pct <= 0.1, error_pct


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


def make_image(path, size, format, index, qcow2_compat, backing=None):
    op = qemuimg.create(path, size=size, format=format,
                        qcow2Compat=qcow2_compat,
                        backing=backing)
    op.run()
    offset = index * 1024
    qemuio.write_pattern(
        path,
        format,
        offset=offset,
        len=1024,
        pattern=0xf0 + index)


class MapMismatch(AssertionError):

    def __init__(self, message, expected, actual):
        self.message = message
        self.expected = expected
        self.actual = actual

    def __str__(self):
        text = self.message + "\n"
        text += "\n"
        text += "Expected map:\n"
        text += pprint.pformat(self.expected) + "\n"
        text += "\n"
        text += "Actual map:\n"
        text += pprint.pformat(self.actual) + "\n"
        return text
