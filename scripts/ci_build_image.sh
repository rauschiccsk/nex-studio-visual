#!/usr/bin/env bash
# Build one image of the cockpit in CI from the cached layers (DEV-219).
#
#   scripts/ci_build_image.sh <backend|frontend> <context> <cache-hit>
#
# The layers live in ~/.image-layers/<name>, restored by the job's cache step, which is keyed by every file the
# packages of the images are installed from. BuildKit reuses each layer whose inputs did not change, so a commit that
# changes only the code downloads no package. On a hit the layers are only read; on a miss every layer is saved for
# the next run (mode=max — the dependency layers of the build stages too).
set -euo pipefail

name=$1
context=$2
hit=${3:-}
layers="$HOME/.image-layers/$name"

args=(buildx build --load -t "nex-studio-visual-$name:ci-$GITHUB_SHA")
if [ -d "$layers" ]; then
    args+=(--cache-from "type=local,src=$layers")
fi
if [ "$hit" != "true" ]; then
    args+=(--cache-to "type=local,dest=$layers,mode=max")
fi
docker "${args[@]}" "$context"
