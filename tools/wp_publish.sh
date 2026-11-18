#!/usr/bin/env bash
# Publish the freelancer guide to WordPress.
#
#   tools/wp_publish.sh ru|en            # dry run: back up live post, build, show diff size
#   tools/wp_publish.sh ru|en --apply    # also update the post on production and verify it
#
# Posts: ru = 248 (/freelancer_guide_v4/), en = 280
# (/freelancers-handbook-in-germany-v0-4-by-ilya-fedotov/). URLs and titles are not touched.
# Every run saves the live content to .wp-backups/ first; WordPress also keeps a revision.
# Requires SSH key access to production, WP-CLI there, and pandoc locally.
set -euo pipefail

LANG_CODE="${1:-}"; APPLY="${2:-}"
case "$LANG_CODE" in
  ru) POST_ID=248 ;;
  en) POST_ID=280 ;;
  *) echo "usage: $0 ru|en [--apply]" >&2; exit 2 ;;
esac
[[ -z "$APPLY" || "$APPLY" == "--apply" ]] || { echo "unknown option: $APPLY" >&2; exit 2; }

REMOTE="${WP_SSH:-famtdqxc@45.82.189.107}"
WP_HOME="${WP_HOME:-/var/www/vhosts/ilyafedotov.com}"
WP_ROOT="${WP_ROOT:-$WP_HOME/httpdocs}"
WP_USER="${WP_USER:-ilya}"   # admin account, so WordPress keeps tables/anchors unfiltered
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$ROOT/.wp-backups/$STAMP-$LANG_CODE"
mkdir -p "$OUT"

wp() { ssh -o BatchMode=yes "$REMOTE" "cd '$WP_ROOT' && wp $*"; }

echo "1/4 Backing up live post $POST_ID -> $OUT/live.html"
wp post get "$POST_ID" --field=post_content > "$OUT/live.html"

echo "2/4 Building new content"
python3 "$ROOT/tools/wp_export.py" --lang "$LANG_CODE" --reference "$OUT/live.html" --out "$OUT/new.html"
if [[ "$LANG_CODE" == ru ]]; then
  # Local WordPress export without the related-reading block (gitignored).
  python3 - "$OUT/new.html" "$ROOT/blog/freelance-guide.ru.html" <<'PY'
import sys
t = open(sys.argv[1], encoding="utf-8").read()
m = "<!-- ilya-related-reading -->"
open(sys.argv[2], "w", encoding="utf-8").write(t[: t.index(m)] if m in t else t)
PY
fi
CHANGED=$(diff "$OUT/live.html" "$OUT/new.html" | grep -c '^[<>]' || true)
echo "    changed lines vs live: $CHANGED   (diff: diff $OUT/live.html $OUT/new.html)"

if [[ "$APPLY" != "--apply" ]]; then
  echo "Dry run only. Re-run with --apply to publish."; exit 0
fi

echo "3/4 Updating post $POST_ID"
TMP="$WP_HOME/wp-publish-$POST_ID-$STAMP.html"
LOCAL_SHA=$(shasum -a 256 "$OUT/new.html" | cut -c1-64)
REMOTE_SHA=$(ssh -o BatchMode=yes "$REMOTE" "umask 077; cat > '$TMP' && sha256sum '$TMP' | cut -c1-64" < "$OUT/new.html")
[[ "$LOCAL_SHA" == "$REMOTE_SHA" ]] || { echo "upload checksum mismatch" >&2; exit 1; }
wp post update "$POST_ID" "'$TMP'" --user="$WP_USER"
ssh -o BatchMode=yes "$REMOTE" "rm -f '$TMP'"
wp post get "$POST_ID" --field=post_content > "$OUT/stored.html"
python3 - "$OUT/new.html" "$OUT/stored.html" <<'PY'
import sys
a, b = (open(p, "rb").read().rstrip(b"\n") for p in sys.argv[1:3])
sys.exit(0 if a == b else "stored content differs from upload")
PY

echo "4/4 Purging caches"
wp litespeed-purge all
wp cache flush
echo "Done. Revisions for post $POST_ID: $(wp post list --post_type=revision --post_parent="$POST_ID" --format=count)"
