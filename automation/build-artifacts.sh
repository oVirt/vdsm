#!/bin/bash

set -xe

# prepare env
BUILDS=$PWD/rpmbuild
EXPORTS=$PWD/exported-artifacts
mkdir -p "$EXPORTS"
cp $PWD/automation/index.html "$EXPORTS"

# autogen may already have been executed by check-patch.sh
if [ ! -f Makefile ]; then
  ./autogen.sh --system --enable-hooks
fi

make
# tests will be done elsewhere
yum-builddep ./vdsm.spec
make PYFLAKES=true PEP8=true NOSE_EXCLUDE=.* rpm

find "$BUILDS" \
    -iname \*.rpm \
    -exec mv {} "$EXPORTS/" \;
find "$PWD" \
    -maxdepth 1 \
    -iname vdsm\*.tar.gz \
    -exec mv {} "$EXPORTS/" \;
