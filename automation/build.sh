#!/bin/bash

source automation/common.sh

prepare_env
build_vdsm

cp $PWD/lib/vdsm/api/vdsm-api.html "$EXPORT_DIR"

yum-builddep ./vdsm.spec
make rpm

find "$BUILDS" \
    -iname \*.rpm \
    -exec mv {} "$EXPORT_DIR/" \;
find "$PWD" \
    -maxdepth 1 \
    -iname vdsm\*.tar.gz \
    -exec mv {} "$EXPORT_DIR/" \;
