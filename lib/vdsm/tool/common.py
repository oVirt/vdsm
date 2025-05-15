# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function


def confirm(msg):
    """
    Display a message to the user and wait for confirmation.

    Arguments:
        msg (string) - message to display

    Returns:
        True if the user confirmed the message, False otherwise
    """
    while True:
        try:
            res = input(msg)
            res = res.strip().lower()
        except KeyboardInterrupt:
            print()
            return False
        if res in ("no", ""):
            return False
        if res == "yes":
            return True
