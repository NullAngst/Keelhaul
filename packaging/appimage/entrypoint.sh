#! /bin/bash
# python-appimage fills in {{ python-executable }} at build time.
# Our package is bundled under $APPDIR/keelhaul by the -x flag in CI.
export PYTHONPATH="${APPDIR}${PYTHONPATH:+:${PYTHONPATH}}"
{{ python-executable }} -s -m keelhaul "$@"
