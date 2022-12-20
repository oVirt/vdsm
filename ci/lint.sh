#!/bin/bash -xe

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

# Activate the tests venv (for containers only)
[ -d /venv ] && source /venv/bin/activate

./autogen.sh --system
make
make lint
