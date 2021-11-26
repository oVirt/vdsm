#!/bin/bash -xe

export EXPORT_DIR="${EXPORT_DIR:-exported-artifacts}"

./autogen.sh --system
make
make rpm

mkdir -p ${EXPORT_DIR}

cp $PWD/lib/vdsm/api/vdsm-api.html "${EXPORT_DIR}"

find ~/rpmbuild \
    -iname '*.rpm' \
    -exec mv {} "${EXPORT_DIR}/" \;

find . \
    -maxdepth 1 \
    -iname 'vdsm*.tar.gz' \
    -exec mv {} "${EXPORT_DIR}/" \;

createrepo_c "${EXPORT_DIR}"
