#!/bin/bash -xe

podman run \
    --env-host \
    --privileged \
    --rm \
    -it \
    --volume `pwd`:/vdsm:Z \
    --volume /run/udev:/run/udev:Z \
    --volume /dev/log:/dev/log:Z \
    quay.io/ovirt/vdsm-test-$DIST \
    bash -c "cd /vdsm && travis/test.sh $TARGETS"
