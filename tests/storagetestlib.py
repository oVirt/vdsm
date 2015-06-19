# Copyright 2015 Red Hat, Inc.
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
import os
import uuid
from contextlib import contextmanager

from testlib import make_file

from storage import sd, blockSD, fileSD


NR_PVS = 2       # The number of fake PVs we use to make a fake VG by default
MDSIZE = 524288  # The size (in bytes) of fake metadata files


class FakeMetadata(dict):
    @contextmanager
    def transaction(self):
        yield


def make_blocksd_manifest(tmpdir=None, metadata=None, sduuid=None):
    if sduuid is None:
        sduuid = str(uuid.uuid4())
    if metadata is None:
        metadata = FakeMetadata()
    manifest = blockSD.BlockStorageDomainManifest(sduuid, metadata)
    if tmpdir is not None:
        manifest.domaindir = tmpdir
        os.makedirs(os.path.join(manifest.domaindir, sduuid, sd.DOMAIN_IMAGES))
    return manifest


def get_random_devices(count=NR_PVS):
    return ['/dev/mapper/{0}'.format(os.urandom(16).encode('hex'))
            for _ in range(count)]


def make_vg(fake_lvm, manifest, devices=None):
    vg_name = manifest.sdUUID
    if devices is None:
        devices = get_random_devices()
    fake_lvm.createVG(vg_name, devices, blockSD.STORAGE_UNREADY_DOMAIN_TAG,
                      blockSD.VG_METADATASIZE)
    fake_lvm.createLV(vg_name, sd.METADATA, blockSD.SD_METADATA_SIZE)

    # Fake the PV information for our metadata LV (example lvs session follows)
    #   # lvs -o devices b19d16a0-06e1-4c92-b959-2019f503c8ac/metadata
    #   Devices
    #   /dev/mapper/360014059e671b7fc2c44169a58c00289(0)
    fake_lvm.lvmd[vg_name][sd.METADATA]['devices'] = \
        '{0}(0)'.format(devices[0])
    return vg_name


def get_metafile_path(domaindir):
    return os.path.join(domaindir, sd.DOMAIN_META_DATA, sd.METADATA)


def make_filesd_manifest(tmpdir, metadata=None):
    sduuid = str(uuid.uuid4())
    domain_path = os.path.join(tmpdir, sduuid)
    make_file(get_metafile_path(domain_path))
    if metadata is None:
        metadata = FakeMetadata()
    manifest = fileSD.FileStorageDomainManifest(domain_path, metadata)
    os.makedirs(os.path.join(manifest.domaindir, sduuid, sd.DOMAIN_IMAGES))
    return manifest


def make_file_volume(domaindir, size, imguuid=None, voluuid=None):
    imguuid = imguuid or str(uuid.uuid4())
    voluuid = voluuid or str(uuid.uuid4())
    volpath = os.path.join(domaindir, "images", imguuid, voluuid)
    mdfiles = [volpath + '.meta', volpath + '.lease']
    make_file(volpath, size)
    for mdfile in mdfiles:
        make_file(mdfile)
    return imguuid, voluuid
