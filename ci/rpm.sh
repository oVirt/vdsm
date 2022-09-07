#!/bin/bash -xe

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

export EXPORT_DIR="${EXPORT_DIR:-exported-artifacts}"

if [ "$GITHUB_EVENT_NAME" = "push" ]; then
    # For merged patches, use stanrad git describe format matching vdsm
    # copr builds.
    # vdsm-4.50.0.2-46.git8dc924a21.el8.src.rpm
    ./autogen.sh --system
else
    # For pull requests or local builds use a timestamp:
    # vdsm-4.50.0.2-202112031326.git8dc924a21.el8.src.rpm
    ./autogen.sh --system --enable-timestamp
fi

make
make rpm

mkdir -p ${EXPORT_DIR}

cp $PWD/lib/vdsm/api/vdsm-api.html "${EXPORT_DIR}"

find $PWD/build \
    -iname '*.rpm' \
    -exec mv {} "${EXPORT_DIR}/" \;

find . \
    -maxdepth 1 \
    -iname 'vdsm*.tar.gz' \
    -exec mv {} "${EXPORT_DIR}/" \;

createrepo_c "${EXPORT_DIR}"
