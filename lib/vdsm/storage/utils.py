# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.storage import constants as sc


def next_generation(current_generation):
    # Increment a generation value and wrap to 0 after MAX_GENERATION
    return (current_generation + 1) % (sc.MAX_GENERATION + 1)
