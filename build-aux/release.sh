#!/bin/sh
# tags and output releases:
#   - v4.9.0   => 0 (upstream clean)
#   - v4.9.0-1 => 1 (downstream clean)
#   - v4.9.0-2-g34e62f   => 0.2.g34e62f (upstream dirty)
#   - v4.9.0-1-2-g34e62f => 1.2.g34e62f (downstream dirty)
AWK_RELEASE='
    BEGIN { FS="-"; OFS="." }
    /^v[0-9]/ {
      if (NF == 1) print 0
      else if (NF == 2) print $2
      else if (NF == 3) print 0, $2, $3
      else if (NF == 4) print $2, $3, $4
    }'
git describe 2> /dev/null \
    | awk "$AWK_RELEASE" | tr -cd '[:alnum:].'
