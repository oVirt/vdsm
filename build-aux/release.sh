#!/bin/sh
# tags and output releases:
#   - v4.9.0   => 0 (upstream clean)
#   - v4.9.0-1 => 1 (downstream clean)
#   - v4.9.0-2-g34e62f1   => 0.2.git34e62f1 (upstream dirty)
#   - v4.9.0-1-2-g34e62f1 => 1.2.git34e62f1 (downstream dirty)
AWK_RELEASE='
    BEGIN { FS="-"; OFS="." }
    /^v[0-9]/ {
      if (NF == 1) print 0
      else if (NF == 2) print $2
      else if (NF == 3) print 0, $2, "git" substr($3, 2)
      else if (NF == 4) print $2, $3, "git" substr($4, 2)
    }'
git describe --match="v[0-9]*" 2> /dev/null \
    | awk "$AWK_RELEASE" | tr -cd '[:alnum:].'
