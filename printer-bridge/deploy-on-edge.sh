#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:-/tmp/larder-printer-bridge.tar.gz}"
ROOT="/opt/larder-printer-bridge"
RELEASES="$ROOT/releases"
SCANNER_ENV="/opt/larder-scanner-logger/.env"

[ -s "$ARCHIVE" ] || { echo "Printer bridge archive is missing" >&2; exit 1; }
[ -s "$SCANNER_ENV" ] || { echo "Scanner bridge credential environment is missing" >&2; exit 1; }
[ -c /dev/usb/lp1 ] || { echo "MUNBYN printer device /dev/usb/lp1 is missing" >&2; exit 1; }

install -d -m 0755 "$RELEASES"
release="$RELEASES/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0755 "$release"
tar -xzf "$ARCHIVE" --strip-components=1 -C "$release" printer-bridge

ln -sfn "$release" "$ROOT/current"
cd "$ROOT/current"
docker compose --env-file "$SCANNER_ENV" up --detach --build --remove-orphans

for _ in $(seq 1 20); do
  if [ "$(docker inspect --format '{{.State.Running}}' larder-printer-bridge 2>/dev/null)" = "true" ]; then
    echo "Larder printer bridge is running with /dev/usb/lp1"
    exit 0
  fi
  sleep 1
done

docker compose --env-file "$SCANNER_ENV" ps
docker compose --env-file "$SCANNER_ENV" logs --tail 50 printer-bridge
echo "Larder printer bridge failed readiness" >&2
exit 1
