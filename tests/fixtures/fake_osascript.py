#!/usr/bin/env python3
# WHY:  tests must observe the digest notification without touching the real macOS notifier.
# WHAT: fake `osascript` (used via BRAIN_NOTIFY_CMD): appends one JSON line {argv, script} to
#       the file named by BRAIN_FAKE_NOTIFY_LOG. Does nothing else.
# WHO:  tests/test_digest_notify.py; never shipped.
import json
import os
import sys

log = os.environ.get("BRAIN_FAKE_NOTIFY_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": sys.argv[1:], "script": sys.argv[2] if len(sys.argv) > 2 else ""}) + "\n")
