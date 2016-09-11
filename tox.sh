#!/bin/sh -e

WHITELIST=(build-aux/vercmp \
           contrib/logdb \
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

for checker in "$@"; do
    if [ 'pyflakes' = "$checker" ]; then
        (find . -path './.tox' -prune -type f -o \
            -path './.git' -prune -type f -o \
            -path './.ropeproject' -prune -type f -o \
            -name '*.py' && echo "${WHITELIST[@]}") | \
            xargs pyflakes | grep -w -v "${SKIP_PYFLAKES_ERR}" | \
            while read LINE; do echo "$LINE"; false; done
    elif [ 'pep8' = "$checker" ]; then
        for x in ${PEP8_BLACKLIST[@]}; do \
        exclude="${exclude},${x}" ; \
            done ; \
            pep8 --exclude="${exclude},.tox,.ropeproject" \
            --filename '*.py' . \
            "${WHITELIST[@]}"
    fi
done
