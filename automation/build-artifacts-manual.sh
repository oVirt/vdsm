#!/bin/bash

set -xe

# prepare env
BUILDS=$PWD/rpmbuild
EXPORTS=$PWD/exported-artifacts
OUTPUT=$PWD/output
mkdir -p "$EXPORTS"

rm -rf $OUTPUT

# create the src.rpm, assuming the tarball is in the directory
rpmbuild \
    -D "_srcrpmdir $OUTPUT" \
    -D "_topmdir $BUILDS" \
    -ts ./vdsm*.tar.gz

# install any build requirements
yum-builddep output/vdsm*.src.rpm

# create the rpms
rpmbuild \
    -D "_rpmdir $OUTPUT" \
    -D "_topmdir $BUILDS" \
    --rebuild output/vdsm*.src.rpm

find "$OUTPUT" \
    -iname \vdsm*.rpm \
    -exec mv {} "$EXPORTS/" \;
find "$PWD" \
    -maxdepth 1 \
    -iname vdsm\vdsm*.tar.gz \
    -exec mv {} "$EXPORTS/" \;
