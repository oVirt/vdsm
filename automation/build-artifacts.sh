#!/bin/bash

source automation/common.sh

prepare_env
build_vdsm

cp $PWD/lib/vdsm/api/vdsm-api.html "$EXPORT_DIR"

# tests will be done elsewhere
yum-builddep ./vdsm.spec
make PYFLAKES="" PEP8="" NOSE_EXCLUDE=.* rpm

find "$BUILDS" \
    -iname \*.rpm \
    -exec mv {} "$EXPORT_DIR/" \;
find "$PWD" \
    -maxdepth 1 \
    -iname vdsm\*.tar.gz \
    -exec mv {} "$EXPORT_DIR/" \;
