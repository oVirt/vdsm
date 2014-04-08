# Copyright 2013 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import glob
import pwd
import selinux

from .. import constants
from ..config import config
from . import expose, ExtraArgsError
from ..utils import execCmd, CommandPath


SELINUX_VIRT_IMAGE_LABEL = "system_u:object_r:virt_image_t:s0"
TRANSIENT_DISKS_REPO = config.get('vars', 'transient_disks_repository')

_fuser = CommandPath(
    "fuser",
    "/sbin/fuser",  # Fedora, EL6
)


@expose("setup-transient-repository")
def setup_transient_repository(*args):
    """
    setup-transient-repository
    Prepare the transient disks repository
    """
    if len(args) > 1:
        raise ExtraArgsError()

    _, _, vdsm_uid, vdsm_gid, _, _, _ = pwd.getpwnam(constants.VDSM_USER)

    try:
        os.makedirs(TRANSIENT_DISKS_REPO)
    except OSError as e:
        if e.errno != os.errno.EEXIST:
            raise

    os.chown(TRANSIENT_DISKS_REPO, vdsm_uid, vdsm_gid)
    os.chmod(TRANSIENT_DISKS_REPO, 0o750)
    selinux.chcon(TRANSIENT_DISKS_REPO, SELINUX_VIRT_IMAGE_LABEL)


@expose("cleanup-transient-repository")
def cleanup_transient_repository(*args):
    """
    cleanup-transient-repository
    Cleanup the unused transient disks present in the repository.
    (NOTE: it is recommended to NOT execute this command when the vdsm
    daemon is running)
    """
    if len(args) > 1:
        raise ExtraArgsError()

    transient_images = set(glob.glob(os.path.join(TRANSIENT_DISKS_REPO, "*")))

    if len(transient_images) == 0:
        return  # Nothing to do

    cmd_ret, cmd_out, cmd_err = execCmd([_fuser.cmd] + list(transient_images))
    # According to: "fuser returns a non-zero return code if none of the
    # specified files is accessed or in case of a fatal error. If at least
    # one access has been found, fuser returns zero." we can discard the
    # return code.
    # NOTE: the list of open files is printed to cmd_err with an extra ":"
    # character appended (removed by [:-1]).
    open_transient_images = set(x[:-1] for x in cmd_err)

    for image_path in transient_images - open_transient_images:
        # NOTE: This could cause a race with the creation of a virtual
        # machine with a transient disk (if vdsm is running).
        try:
            os.unlink(image_path)
        except OSError as e:
            if e.errno != os.errno.ENOENT:
                raise
