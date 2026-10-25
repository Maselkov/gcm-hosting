#!/usr/bin/env bash
# The one thing the deploy key may run (deploy/README.md): its
# authorized_keys line forces this, and the command it was asked for comes
# in as SSH_ORIGINAL_COMMAND, "<testing|production> <tag>". Deploys
# ghcr.io/maselkov/gcm-hosting:<tag> as that instance, with the compose
# files from inside that image - never ones the caller sends, which could
# mount anything. If the new release doesn't come up healthy, the one
# before it goes back up and this fails.
#
# Installed by hand, not by a deploy: it's what keeps the key from being
# a shell.
set -euo pipefail

ROOT=${GCM_DEPLOY_ROOT:-/srv/gcm-hosting}
REPO=ghcr.io/maselkov/gcm-hosting

fail() {
    echo "deploy: $*" >&2
    exit 2
}

read -r env tag extra <<<"${SSH_ORIGINAL_COMMAND:-}" || true
case ${env:-} in
testing | production) ;;
*) fail "usage: <testing|production> <tag>" ;;
esac
[[ -z ${extra:-} ]] || fail "usage: <testing|production> <tag>"
# Production only gets releases (v1.2.3 -> 1.2.3); testing also gets
# commits (sha-<short>).
if [[ ${tag:-} =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    :
elif [[ $env == testing && ${tag:-} =~ ^sha-[0-9a-f]{7,40}$ ]]; then
    :
else
    fail "not a tag $env takes: ${tag:-(none)}"
fi

dir=$ROOT/$env
[[ -f $dir/.env ]] || fail "no $dir/.env"
[[ -f $ROOT/edge/.env ]] || fail "no $ROOT/edge/.env"
# One deploy at a time on this host: both instances share the edge.
exec 9>"$ROOT/.deploy.lock"
flock 9
files=$(mktemp -d)
trap 'rm -rf "$files"' EXIT

edge_running() {
    [[ -n $(cd "$ROOT/edge" && docker compose -p gcm-edge ps -q --status running traefik 2>/dev/null) ]]
}

# Every step checked by hand: set -e doesn't apply inside a function
# called from an if.
up() {
    local image=$REPO:$1 cid
    echo "deploy: $env <- $image"
    if ! docker pull -q "$image"; then
        docker image inspect "$image" >/dev/null 2>&1 || return 1
        echo "deploy: couldn't pull $image, using the copy already here"
    fi
    rm -rf "${files:?}"/* || return 1
    cid=$(docker create "$image") || return 1
    docker cp -q "$cid:/app/deploy/." "$files" || { docker rm "$cid" >/dev/null; return 1; }
    docker rm "$cid" >/dev/null || return 1
    # With the image written in, so docker compose works in $dir by hand.
    sed "s|\${APP_IMAGE:?}|$image|" "$files/compose.app.yml" >"$dir/compose.yml" || return 1
    # Only production changes the proxy production depends on; testing
    # starts it only if nothing has yet.
    if [[ $env == production ]] || ! edge_running; then
        cp "$files/compose.edge.yml" "$ROOT/edge/compose.yml" || return 1
        (cd "$ROOT/edge" && docker compose -p gcm-edge up -d --remove-orphans) || return 1
    fi
    (cd "$dir" && docker compose -p "gcm-$env" up -d --remove-orphans --wait --wait-timeout 120)
}

previous=$(cat "$dir/deployed" 2>/dev/null || true)
if up "$tag"; then
    echo "$tag" >"$dir/deployed"
    # Releases of this app unused for a week; the bases' images aren't
    # labelled with this repo, so they stay.
    docker image prune -af --filter "label=org.opencontainers.image.source=https://github.com/Maselkov/gcm-hosting" \
        --filter until=168h >/dev/null || true
    echo "deploy: $env is on $tag"
    exit 0
fi

echo "deploy: $tag didn't come up healthy" >&2
(cd "$dir" && docker compose -p "gcm-$env" logs --tail 50 hosting) >&2 || true
if [[ -n $previous && $previous != "$tag" ]]; then
    echo "deploy: going back to $previous" >&2
    up "$previous" || echo "deploy: $previous didn't come up either" >&2
fi
exit 1
