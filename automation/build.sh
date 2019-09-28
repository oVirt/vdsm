#!/bin/bash

source automation/common.sh

prepare_env
build_vdsm

cp $PWD/lib/vdsm/api/vdsm-api.html "$EXPORT_DIR"

# 'target_py' macro is set to 'py2' or 'py3' depending
# on the value of 'CI_PYTHON' env variable. Tests will be done elsewhere.
yum-builddep --define "target_py ${CI_PYTHON/thon/}" ./vdsm.spec
make rpm

find "$BUILDS" \
    -iname \*.rpm \
    -exec mv {} "$EXPORT_DIR/" \;
find "$PWD" \
    -maxdepth 1 \
    -iname vdsm\*.tar.gz \
    -exec mv {} "$EXPORT_DIR/" \;
