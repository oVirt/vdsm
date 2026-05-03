# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later


def tobool(s):
    try:
        if s is None:
            return False
        if isinstance(s, bool):
            return s
        if s.lower() == 'true':
            return True
        return bool(int(s))
    except:
        return False
