#!/bin/sh
set -eu

secret_file=/run/secrets/typesafe_api_key
if [ ! -r "$secret_file" ]; then
    printf '%s\n' '{"event":"clean_room_error","reason":"missing_typesafe_secret"}'
    exit 2
fi

cd /opt/herdr-jev-router
uv run pytest
uv run ruff check .
uv run ruff format --check .

# Packaging smoke test: install the wheel built by `uv build` into a fresh
# virtual environment outside the project and exercise every console script
# declared in pyproject.toml under [project.scripts]. The list is read from
# pyproject.toml so it cannot drift from the declared entry points.
console_scripts=$(python3 -c 'import tomllib; print(" ".join(tomllib.load(open("pyproject.toml", "rb"))["project"]["scripts"]))')
if [ -z "$console_scripts" ]; then
    printf '%s\n' '{"event":"clean_room_error","reason":"no_console_scripts"}'
    exit 2
fi
install_root=$(mktemp -d /tmp/herdr-jev-install.XXXXXX)
python3 -m venv "$install_root/venv"
uv pip install --python "$install_root/venv/bin/python" dist/*.whl
for script in $console_scripts
do
    "$install_root/venv/bin/$script" --help >/dev/null
    printf '{"event":"console_script_ok","script":"%s"}\n' "$script"
done
rm -rf "$install_root"

git init --quiet
git add --all
git diff --cached --check

# One real Jev recommendation through the advisory explain command. explain
# audits the decision and never starts an agent, so the clean room needs no
# Herdr server. The router removes a harness that is missing from PATH before
# Jev, so the clean room provides a stub claude executable.
state_dir=$(mktemp -d)
harness_bin=$(mktemp -d)
trap 'rm -rf "$state_dir" "$harness_bin"' EXIT HUP INT TERM
printf '#!/bin/sh\nexit 0\n' >"$harness_bin/claude"
chmod 755 "$harness_bin/claude"
PATH="$harness_bin:$PATH"
export PATH
review_file="$state_dir/review.txt"

TYPESAFE_API_KEY=$(cat "$secret_file")
export TYPESAFE_API_KEY
HERDR_JEV_ROUTER_STATE_DIR="$state_dir" \
    uv run herdr-jev-router explain \
    "Inspect a small code change and report correctness risks." \
    --role reviewer --read-only >"$review_file"

python3 - "$review_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    lines = [line.strip() for line in stream if line.strip()]

fields = dict(line.split(": ", 1) for line in lines if ": " in line)
harness = fields["recommended harness"]
model = fields["selected model"]
effort = fields["effort"]
assert harness in {"claude", "codex", "opencode", "pi"}
assert effort in {"low", "medium", "high", "xhigh", "max"}
print(
    json.dumps(
        {
            "event": "jev_recommendation_ok",
            "harness": harness,
            "model": model,
            "effort": effort,
        },
        separators=(",", ":"),
    )
)
PY

printf '%s\n' '{"event":"clean_room_ok"}'
