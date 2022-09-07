# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.common import cmdutils
from vdsm.storage import multipath

from . marks import requires_root

MULTIPATHD_SCRIPT = """\
#!/bin/sh

# Normally, we would run the real multipathd to validate the arguments and
# dropping the output with
#
# multipathd "$@" > /dev/null
#
# However, multipathd requires /etc/multipath.conf to be present and also DM
# multipath kernel driver to be loaded, otherwise fails. As this is not by
# default configured and enabled, skip the multipath test run and just fake the
# output.

echo '{}'
"""

# scsi_id output from existing device which has ID_SERIAL
FAKE_SCSI_ID_OUTPUT = """\
ID_SCSI=1
ID_VENDOR=ATA
ID_VENDOR_ENC=ATA\x20\x20\x20\x20\x20
ID_MODEL=WDC_WD2502ABYS-1
ID_MODEL_ENC=WDC\x20WD2502ABYS-1
ID_REVISION=3B05
ID_TYPE=disk
ID_SERIAL=SATA_WDC_WD2502ABYS-1_WD-WMAT16865419
ID_SERIAL_SHORT=WD-WMAT16865419
"""

# scsi_id output from existing device which hasn't ID_SERIAL
FAKE_SCSI_ID_NO_SERIAL = """\
ID_SCSI=1
ID_VENDOR=Lenovo
ID_VENDOR_ENC=Lenovo\x20\x20
ID_MODEL=CDROM
ID_MODEL_ENC=CDROM\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20
ID_REVISION=2.31
ID_TYPE=cd
"""

# scsi_id output run against device which doesn't exist
FAKE_SCSI_ID_MISSING_DEVICE = """\
ID_SCSI=1
ID_VENDOR=
ID_VENDOR_ENC=
ID_MODEL=
ID_MODEL_ENC=
ID_REVISION=
ID_TYPE=
"""

SCSI_ID_SCRIPT = """\
#!/bin/sh
set -e

# Run the real scsi_id to validate the arguments, dropping the output.
# This path is valid on Fedora and CentOS, but as we test only on these OSs, it
# should be fine.
/usr/lib/udev/scsi_id "$@" > /dev/null

# Fake the output
echo '{}'
"""


@pytest.fixture
def fake_multipathd(monkeypatch, fake_executable):
    monkeypatch.setattr(
        multipath,
        "_MULTIPATHD",
        cmdutils.CommandPath("fake-multipathd", str(fake_executable))
    )

    return fake_executable


@pytest.fixture
def fake_scsi_id(monkeypatch, fake_executable):
    monkeypatch.setattr(
        multipath,
        "_SCSI_ID",
        cmdutils.CommandPath("fake-scsi_id", str(fake_executable))
    )

    return fake_executable


@requires_root
@pytest.mark.root
def test_resize_map(fake_multipathd):
    fake_multipathd.write(MULTIPATHD_SCRIPT.format("ok"))
    multipath.resize_map("fake_device")


@requires_root
@pytest.mark.root
def test_resize_map_failed(fake_multipathd):
    fake_multipathd.write(MULTIPATHD_SCRIPT.format("fail"))

    with pytest.raises(multipath.Error):
        multipath.resize_map("fake_device")


@requires_root
@pytest.mark.root
def test_scsi_id(fake_scsi_id):
    fake_scsi_id.write(SCSI_ID_SCRIPT.format(FAKE_SCSI_ID_OUTPUT))

    scsi_serial = multipath.get_scsi_serial("fake_device")
    assert scsi_serial == "SATA_WDC_WD2502ABYS-1_WD-WMAT16865419"


@requires_root
@pytest.mark.root
def test_scsi_id_no_serial(fake_scsi_id):
    fake_scsi_id.write(SCSI_ID_SCRIPT.format(FAKE_SCSI_ID_NO_SERIAL))

    scsi_serial = multipath.get_scsi_serial("fake_device")
    assert scsi_serial == ""

    fake_scsi_id.write(SCSI_ID_SCRIPT.format(FAKE_SCSI_ID_MISSING_DEVICE))

    scsi_serial = multipath.get_scsi_serial("fake_device")
    assert scsi_serial == ""


@requires_root
@pytest.mark.root
def test_scsi_id_fails(fake_scsi_id):
    fake_scsi_id.write("#!/bin/sh\nexit 1\n")

    scsi_serial = multipath.get_scsi_serial("fake_device")
    assert scsi_serial == ""
