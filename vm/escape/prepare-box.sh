#!/usr/bin/env bash
# Runs only in a separate, newly created builder VM, never an experimental VM.
set -euo pipefail
cd /workspace
source vm/escape/common.sh
lab_stage 'prepare all escape dependencies' lab_dependencies all
lab_stage 'prepare escape image' lab_image
# No challenges, flags, grants, model credentials or state volumes are created.
printf '[lab] prepared box ready: %ss total\n' "$SECONDS"
