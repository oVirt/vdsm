#!/bin/sh -e

WHITELIST=(contrib/logdb \
           contrib/logstat \
           contrib/profile-stats \
           init/daemonAdapter \
           vdsm/get-conf-item \
           vdsm/set-conf-item \
           vdsm/supervdsmServer \
           vdsm/vdsm \
           vdsm/vdsm-restore-net-config \
           vdsm/storage/curl-img-wrap \
           vdsm/storage/fc-scan \
           vdsm-tool/vdsm-tool
          )

SKIP_PYFLAKES_ERR="\./vdsm/storage/lvm\.py.*: list comprehension redefines \
       'lv' from line .*"

PEP8_BLACKLIST=(config.py \
                constants.py \
                crossImportsTests.py \
                vdsm.py \
               )

if [ 'pyflakes' = "$1" ]; then
    (find . -path './.tox' -prune -type f -o \
        -path './.git' -prune -type f -o \
        -name '*.py' && echo "${WHITELIST[@]}") | \
        xargs pyflakes | grep -w -v "${SKIP_PYFLAKES_ERR}" | \
        while read LINE; do echo "$LINE"; false; done
fi

if [ 'pep8' = "$1" ]; then
    for x in ${PEP8_BLACKLIST[@]}; do \
    exclude="${exclude},${x}" ; \
        done ; \
        pep8 --exclude="${exclude},.tox" \
        --filename '*.py' . \
        "${WHITELIST[@]}"
fi
