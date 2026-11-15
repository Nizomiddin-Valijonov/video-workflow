"""Beriladigan paket ichida shaxsiy ma'lumot qolmaganini tekshiradi.

Ishlatish:
    .venv-local/bin/python -I tools/inspect_bundle.py dist/VideoWorkflow-Windows-x64-NoDocker.zip
"""
from __future__ import annotations

import collections
import sys
import zipfile

RISK = (
    "storage/", "data/", ".env", "batch_session", "batch_result",
    "batch_state", "users.json", ".sqlite", ".log", "downloads/",
)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("ishlatish: inspect_bundle.py <zip>")
        return 2
    path = argv[1]
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
    print("FAYL:", len(names))

    tops = collections.Counter(n.split("/")[0] for n in names)
    print("\nEng yuqori daraja:")
    for k, v in sorted(tops.items()):
        print("  %6d  %s" % (v, k))

    pref = collections.Counter("/".join(n.split("/")[:2]) for n in names)
    print("\n2-daraja (birinchi 40):")
    for k, v in sorted(pref.items())[:40]:
        print("  %6d  %s" % (v, k))

    print("\nXavfli naqshlar:")
    risky = 0
    for pat in RISK:
        hits = [n for n in names if pat in n]
        risky += len(hits)
        print("  %-16s %d  %s" % (pat, len(hits), hits[:4]))
    print("\nJAMI xavfli yozuv:", risky)

    probe = [
        "app/backend/app/main.py",
        "app/backend/workers/runner.py",
        "app/frontend/out/index.html",
        "runtime/python/python.exe",
        "VideoWorkflow.exe",
    ]
    print("\nMuhim fayllar:")
    norm = {n[len("vw-win-bundle/"):] if n.startswith("vw-win-bundle/") else n for n in names}
    for p in probe:
        print("  %-36s %s" % (p, "BOR" if p in norm else "YO'Q"))

    browsers = sorted({n.split("/")[2] for n in norm if n.startswith("browsers/") and len(n.split("/")) > 2})
    print("\nbrowsers/ ichida:", browsers[:10])

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
