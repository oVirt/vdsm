# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.common import config
from . import expose


@expose("show-default-config")
def show_default_config(*args):
    """
    show-default-config

    Prints the default configuration used for VDSM.
    """
    config.print_config()
