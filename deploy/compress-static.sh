#!/bin/bash
# Build the .gz companions that nginx's gzip_static serves.
#
# nginx compressing a response itself has to use chunked encoding, which some
# proxies stall on. A .gz already on disk has a known size, so nginx can send
# a plain Content-Length instead. See deploy/nginx-ihwss.conf.
#
# Run from the project root after changing any CSS or JavaScript:
#
#   bash deploy/compress-static.sh
#
# Safe to re-run; existing .gz files are replaced. Nothing here needs root.
set -euo pipefail

STATIC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/app/static"
[ -d "$STATIC" ] || { echo "no such directory: $STATIC" >&2; exit 1; }

# Only text formats. JPEG and PNG are already compressed, and a .gz of one is
# usually larger than the original.
mapfile -t FILES < <(find "$STATIC" \
    \( -name '*.css' -o -name '*.js' -o -name '*.svg' -o -name '*.json' \) \
    ! -name '*.gz' -type f)

for f in "${FILES[@]}"; do
    # -9 best compression, -k keep the original, -f overwrite a stale .gz.
    gzip -9 -k -f "$f"
    # nginx compares mtimes and will ignore a .gz older than its source.
    touch -r "$f" "$f.gz"
    printf '  %-46s %7s -> %s bytes\n' \
        "${f#"$STATIC"/}" "$(stat -c%s "$f")" "$(stat -c%s "$f.gz")"
done

echo "compressed ${#FILES[@]} file(s)"
