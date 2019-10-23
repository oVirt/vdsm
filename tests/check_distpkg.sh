#!/bin/sh -e
# Check that the distibution tarball does not contain generated files.

DIST=$1

if ! [ -f "$DIST" ]; then
    echo "ERROR: Distribution package at $DIST"
    exit 1
fi

# Verify that generated files are not included in dist package
DIST_LIST=$(mktemp)
DIR=$(basename "$DIST" .tar.gz)
tar tzf "$DIST" > "$DIST_LIST"
for i in $(git ls-files \*.in); do
    FILE=$(echo "$i" | sed -e 's/.in$//')

    # There are some files that we want to be included
    KEEP=0
    for f in \
        static/libexec/vdsm/vdsm-gencerts.sh \
        static/usr/share/man/man1/vdsm-tool.1 \
        vdsm.spec \
    ; do
        if test "$FILE" = "$f" ; then
            KEEP=1
            break
        fi
    done
    test "$KEEP" -eq 1 && continue

    if grep -q -F -x "$DIR/$FILE" "$DIST_LIST"; then
        echo "ERROR: Distribution package contains generated file $FILE"
        exit 1
    fi
done
# TODO: delete also on failures.
rm -f "$DIST_LIST"
