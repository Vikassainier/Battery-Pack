#!/usr/bin/env bash
# Syntax-check every front-end ES module with node (no bundler needed).
set -e
cd "$(dirname "$0")/../battery_thermal/api/static/js"
tmp=$(mktemp -d)
status=0
for f in *.js; do
  cp "$f" "$tmp/$f.mjs"
  if ! node --check "$tmp/$f.mjs" 2>"$tmp/err"; then echo "SYNTAX ERROR in $f"; cat "$tmp/err" | head -8; status=1; fi
done
[ $status -eq 0 ] && echo "JS syntax OK ($(ls *.js | wc -l) modules)"
exit $status
