# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import threading

vars = threading.local()
vars.task = None
vars.context = None
