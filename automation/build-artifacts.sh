#!/bin/bash

set -xe

# a horrible hack around CentOS's ppc dist name change
# instead of this hack, ovirt ci must not bundle x86 and ppc packages together,
# following CentOS's annoying standards.
if [ -f /etc/rpm/macros.dist ]; then
    sed -i s/.centos.p// /etc/rpm/macros.dist
fi

# prepare env
BUILDS=$PWD/rpmbuild
EXPORTS=$PWD/exported-artifacts
mkdir -p "$EXPORTS"
cp $PWD/automation/index.html "$EXPORTS"

# autogen may already have been executed by check-patch.sh
if [ ! -f Makefile ]; then
  ./autogen.sh --system --enable-hooks --enable-vhostmd
fi

make

cp $PWD/lib/vdsm/api/vdsm-api.html "$EXPORTS"

# tests will be done elsewhere
yum-builddep ./vdsm.spec
make PYFLAKES="" PEP8="" NOSE_EXCLUDE=.* rpm

find "$BUILDS" \
    -iname \*.rpm \
    -exec mv {} "$EXPORTS/" \;
find "$PWD" \
    -maxdepth 1 \
    -iname vdsm\*.tar.gz \
    -exec mv {} "$EXPORTS/" \;
