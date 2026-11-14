# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Fetch every implemented source. With AFTERCROP_OFFLINE=1 it proves the cache is
complete (criterion M1.1): exit 0 and ``network_calls=0`` in the log.

A failing source does not stop the others; the exit code reports it.

Run from the repository root:  python -m pipeline.adapters.fetch_all [--only zarc power soilgrids]
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime, timezone

from pipeline.adapters import base, power, soilgrids, zarc

SOURCES = {
    "zarc": lambda: zarc.run()["zarc_status"],
    "power": lambda: f"{len(power.run()['files'])} files",
    "soilgrids": lambda: soilgrids.access_test()["service"],
}
LOG = base.DATA_DIR / "logs" / "fetch_all.log"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", choices=sorted(SOURCES), default=sorted(SOURCES))
    args = ap.parse_args(argv)
    lines, failed = [], []
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines.append(f"{stamp} offline={int(base.is_offline())} sources={','.join(args.only)}")
    for name in args.only:
        try:
            lines.append(f"{name}: ok ({SOURCES[name]()})")
        except Exception as e:
            failed.append(name)
            lines.append(f"{name}: FAILED {type(e).__name__}: {e}")
            traceback.print_exc(file=sys.stderr)
    lines.append(f"network_calls={base.network_calls}")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
