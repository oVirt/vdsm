#!/bin/bash -xe

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

# Only when running in a container
[ -d /venv ] && {
    # Workaround to avoid this warning:
    #   fatal: detected dubious ownership in repository at '/dir'
    git config --global --add safe.directory "$(pwd)"

    # Activate the tests venv (for containers only)
    source /venv/bin/activate
}

# Force colored output for linting tools
export FORCE_COLOR=1
export PY_COLORS=1

./autogen.sh --system
make
make lint
