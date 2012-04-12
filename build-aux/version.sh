#!/bin/sh
# tags and output versions:
#   - v4.9.0   => 4.9.0 (upstream clean)
#   - v4.9.0-1 => 4.9.0 (downstream clean)
#   - v4.9.0-2-g34e62f   => 4.9.0 (upstream dirty)
#   - v4.9.0-1-2-g34e62f => 4.9.0 (downstream dirty)
AWK_VERSION='
    BEGIN { FS="-" }
    /^v[0-9]/ {
      sub(/^v/,"") ; print $1
    }'

git describe --match "v[0-9]*" 2> /dev/null \
    | awk "$AWK_VERSION" | tr -cd '[0-9].'
