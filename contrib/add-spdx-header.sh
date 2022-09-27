#!/usr/bin/env bash
#
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Simple script to add SPDX copyright notice header lines to new files
# using reuse.
#
# The script expects at least one argument (i.e., the FILE).
# It can accept more arguments, that will be passed as CLI options
# to reuse, but does not check for the validity of any argument.
# Therefore wrong arguments may trigger reuse errors.
#
# Default options:
# - Use 'Red Hat, Inc.' as default Copyright holder.
# - GPL-2.0-or-later as default license for new files in the project.
# - Use vdsm-specific notice template.
# - Exclude years, as they are not required and makes it easier to mantain.
#
# Usage:
#
#   contrib/add-spdx-header.sh [OPTIONS] FILE
#
# Examples:
#
#   contrib/add-spdx-header.sh new_file.py
#   contrib/add-spdx-header.sh --style html new_file
#   contrib/add-spdx-header.sh --explicit-license new_file.txt


if [ -z "$1" ]; then
    echo "ERROR: File missing"
    echo "Usage:"
    echo "  $0 [OPTIONS] FILE"
    exit 1
fi

reuse addheader \
    --copyright="Red Hat, Inc." \
    --license="GPL-2.0-or-later" \
    --template=vdsm.jinja2 \
    --exclude-year \
    "$@"
