#!/usr/bin/env bash
#
#  Copyright (c) 2022 Sameer Al Sahab
#  Licensed under the MIT License. See LICENSE file for details.
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
#
# apkmirror-dl.sh — Download APKs from APKMirror
#
# Usage:
#   ./apkmirror-dl.sh [OPTIONS] <URL>
#
# URL can be any of:
#   - APP page:      https://www.apkmirror.com/apk/<org>/<app>/   (newest release is used)
#   - RELEASE page:  .../<app>-<version>-release/
#   - VARIANT page:  .../<app>-<version>-android-apk-download/   (skips steps 1-2)
#
# Options:
#   -a, --arch ARCH       Only accept variants for this arch (e.g. arm64-v8a, universal)
#   -d, --dpi DPI         Only accept variants for this dpi  (e.g. nodpi, 480dpi)
#   -o, --output FILE     Save as FILE (default: name sent by the server)
#   -l, --latest          Accepted for compatibility (app pages always use newest)
#   -q, --quiet           Less chatter
#   -h, --help            Show this help
#
# Env:
#   DEBUG=1               Save every fetched page to ./apkmirror-debug-NN.html
#   CURL_BIN=...          Use another curl (e.g. curl-impersonate-chrome) - strongly recommended
#
# Requires: curl, GNU grep (with -P), sed
#


set -euo pipefail

CURL_BIN="${CURL_BIN:-curl}"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
BASE="${APKMIRROR_BASE:-https://www.apkmirror.com}"



ARCH=""; DPI=""; OUTFILE=""; QUIET=0; URL=""; DEBUG="${DEBUG:-0}"

die() { echo "error: $*" >&2; exit 1; }
say() { if [ "$QUIET" -eq 0 ]; then echo ">> $*" >&2; fi; }

while [ $# -gt 0 ]; do
  case "$1" in
    -l|--latest) shift ;;
    -a|--arch)   ARCH="${2:?--arch needs a value}"; shift 2 ;;
    -d|--dpi)    DPI="${2:?--dpi needs a value}"; shift 2 ;;
    -o|--output) OUTFILE="${2:?--output needs a value}"; shift 2 ;;
    -q|--quiet)  QUIET=1; shift ;;
    -h|--help)   sed -n '2,33p' "$0" | sed 's/^#\{0,1\} \{0,1\}//'; exit 0 ;;
    -*)          die "unknown option: $1" ;;
    *)           URL="$1"; shift ;;
  esac
done

[ -n "$URL" ] || { "$0" --help; exit 1; }
# accept apkmirror.com without www, and bare "org/app" paths
URL="$(sed -E 's#^(https?://)apkmirror\.com#\1www.apkmirror.com#' <<<"$URL")"
grep -qE '^https?://' <<<"$URL" || URL="$BASE/apk/${URL#/}"
command -v "$CURL_BIN" >/dev/null || die "$CURL_BIN not found"



COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT
PAGE_NO=0
PAGE_OUT=""


fetch() {
  local url="$1" code tmp title
  PAGE_NO=$((PAGE_NO+1))
  tmp="$(mktemp)"
  code="$("$CURL_BIN" -sL --compressed -A "$UA" \
        -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" \
        -H "Accept-Language: en-US,en;q=0.9" \
        -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
        -w '%{http_code}' -o "$tmp" "$url" || true)"
  PAGE_OUT="$(cat "$tmp")"; rm -f "$tmp"
  if [ "$DEBUG" = "1" ]; then
    printf '%s' "$PAGE_OUT" > "apkmirror-debug-$(printf '%02d' "$PAGE_NO").html"
  fi
  if [ "$code" = "403" ] || [ "$code" = "503" ]; then
    die "HTTP $code from $url - Cloudflare is probably blocking plain curl. Use CURL_BIN=curl_chrome116 (curl-impersonate) or another IP."
  fi
  [ "$code" = "200" ] || die "HTTP '${code:-none}' fetching $url"
  title="$(grep -oiPm1 '<title>\K[^<]+' <<<"$PAGE_OUT" || true)"
  case "$title" in
    *"Just a moment"*|*"Attention Required"*|*"Access denied"*|*"You have been blocked"*|*"Rate limited"*)
      die "Cloudflare challenge (page title: \"$title\"). Use curl-impersonate or a different IP." ;;
  esac
  grep -qi 'apkmirror' <<<"$PAGE_OUT" || die "page from $url doesn't look like APKMirror (DEBUG=1 to inspect)"
  sleep 1
}

abs_url() {
  case "$1" in
    http*) echo "$1" ;;
    /*)    echo "$BASE$1" ;;
    *)     die "cannot resolve relative URL: $1" ;;
  esac
}


hrefs() { { grep -oP 'href="\K[^"]+' || true; } | sed 's/&amp;/\&/g'; }

pick_href() {  # pick_href <html> <ere-regex>
  local hit
  hit="$(hrefs <<<"$1" | grep -m1 -E "$2" || true)"
  [ -n "$hit" ] || die "could not find link matching /$2/ (DEBUG=1 to inspect fetched page)"
  printf '%s' "$hit"
}

strip_tags() { sed -e 's/<[^>]*>/ /g' -e 's/&amp;/\&/g' | tr -s '[:space:]' ' '; }



fetch "$URL"; HTML="$PAGE_OUT"

if grep -qE '\-download/?$' <<<"$URL"; then

  say "variant page given directly"
  VARIANT_HTML="$HTML"
else

  APP_RE="^${BASE//./\\.}/apk/([^/]+)/([^/]+)/?$"
  if [[ "$URL" =~ $APP_RE ]]; then
    APP_PATH="/apk/${BASH_REMATCH[1]}/${BASH_REMATCH[2]}/"
    say "resolving latest release from app page..."
    REL="$(pick_href "$HTML" "^${APP_PATH}[^/]+-release/?\$")"
    URL="$(abs_url "$REL")"
    say "latest release: $URL"
    fetch "$URL"; HTML="$PAGE_OUT"
  fi

  grep -qE '\-release/?$' <<<"$URL" \
    || die "URL is neither an app page, release page, nor variant page: $URL"


  say "picking variant..."
  BEST=""; BEST_SCORE=-100
  ROWS="$(tr '\r\n' '  ' <<<"$HTML" | sed 's/<div class="table-row/\n&/g' | grep -- '-download/' || true)"
  while IFS= read -r row; do
    [ -n "$row" ] || continue
    href="$(grep -oP 'href="\K[^"]*-download/' <<<"$row" | sed -n '1p' || true)"
    [ -n "$href" ] || continue
    text="$(strip_tags <<<"$row")"

    score=0
    grep -qi 'bundle' <<<"$text" && score=$((score-4))        # app bundle (.apkm)
    if [ -n "$ARCH" ]; then
      grep -qiF -- "$ARCH" <<<"$text" || continue
      score=$((score+10))
    else
      grep -qiE 'universal|noarch' <<<"$text" && score=$((score+5))
    fi
    if [ -n "$DPI" ]; then
      grep -qiF -- "$DPI" <<<"$text" || continue
      score=$((score+10))
    else
      grep -qi 'nodpi' <<<"$text" && score=$((score+5))
    fi

    say "  candidate: ${text:0:90} (score $score)"
    if [ "$score" -gt "$BEST_SCORE" ]; then BEST_SCORE=$score; BEST="$href"; fi
  done <<<"$ROWS"

  if [ -z "$BEST" ] && [ -z "$ARCH$DPI" ]; then
    # layout not recognised: fall back to first variant link on the page
    BEST="$(hrefs <<<"$HTML" | grep -E '/apk/.+-download/?$' | sed -n '1p' || true)"
  fi
  [ -n "$BEST" ] || die "no variant found (matching your filters). DEBUG=1 to inspect the release page"

  VARIANT_URL="$(abs_url "$BEST")"
  say "selected variant page: $VARIANT_URL"
  fetch "$VARIANT_URL"; VARIANT_HTML="$PAGE_OUT"
fi


DL_PAGE_URL="$(abs_url "$(pick_href "$VARIANT_HTML" '/apk/.+/download/')")"
say "opening download page: $DL_PAGE_URL"
fetch "$DL_PAGE_URL"; DL_HTML="$PAGE_OUT"


FILE_URL="$(abs_url "$(pick_href "$DL_HTML" 'download\.php\?id=')")"
say "downloading: $FILE_URL"

COMMON=(-L --compressed -A "$UA" -H "Referer: $DL_PAGE_URL" -b "$COOKIE_JAR" -c "$COOKIE_JAR" --progress-bar)
if [ -n "$OUTFILE" ]; then
  "$CURL_BIN" "${COMMON[@]}" -o "$OUTFILE" "$FILE_URL" || die "download failed"
else

  OUTFILE="$("$CURL_BIN" "${COMMON[@]}" -OJ -w '%{filename_effective}' "$FILE_URL")" || die "download failed"
fi


if head -c 100 "$OUTFILE" | grep -qi '<html\|<!doctype'; then
  die "got an HTML page instead of an APK (saved as $OUTFILE). Link expired or blocked - try DEBUG=1."
fi
if command -v file >/dev/null 2>&1; then file "$OUTFILE"; fi
ls -lh "$OUTFILE"
