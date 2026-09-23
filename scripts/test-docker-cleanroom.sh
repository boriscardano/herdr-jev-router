#!/bin/sh
set -eu

if [ -z "${TYPESAFE_API_KEY:-}" ]; then
    printf '%s\n' "TYPESAFE_API_KEY is required" >&2
    exit 2
fi

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
secret_file=$(mktemp /tmp/herdr-jev-typesafe.XXXXXX)
trap 'rm -f "$secret_file"' EXIT HUP INT TERM
chmod 600 "$secret_file"
printf '%s' "$TYPESAFE_API_KEY" >"$secret_file"

image=herdr-jev-router-cleanroom:local
docker build --file "$repo_dir/Dockerfile.cleanroom" --tag "$image" "$repo_dir"
docker run --rm \
    --mount "type=bind,source=$secret_file,target=/run/secrets/typesafe_api_key,readonly" \
    "$image"
