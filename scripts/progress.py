#!/usr/bin/env python3
"""Progress calculator for the Akatsukii team plans.

Reads the subtask tables in docs/team/*_WORK_PLAN.md (columns: ID | Subtask | Pts | Due | Done %),
and computes earned points per work package, per member and for the team.

Usage:
  python scripts/progress.py             # print a summary and check the files
  python scripts/progress.py --write     # update the generated summary block in each member file
  python scripts/progress.py --rollup    # same as --write, plus refresh the team table in docs/team/README.md
"""
import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEAM = ROOT / "docs" / "team"
MEMBERS = [
    ("ILHAM_WORK_PLAN.md", "Ilham MM"),
    ("KAVIBARATH_WORK_PLAN.md", "Kavibarath S"),
    ("THARSHANTH_WORK_PLAN.md", "Tharshanth A"),
    ("KAJANAN_WORK_PLAN.md", "Kajanan K"),
]
TEAM_TOTAL = 100.0

HEAD = re.compile(r"^####\s+(WP\d+)\s+[–-]\s+(.+?)\s+\(([\d.]+)\s*pts\)\s*$")
ROW = re.compile(r"^\|\s*(\d+)\.(\d+)\s*\|(.*)\|\s*([\d.]+)\s*\|\s*([^|]*?)\s*\|\s*([\d.]+)%\s*\|\s*$")


def fmt(x):
    s = f"{x:.1f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def parse(path):
    """Return (list of WPs, list of problems). WP = dict(id, title, weight, pts, earned)."""
    wps, problems, cur = [], [], None
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        h = HEAD.match(line)
        if h:
            cur = dict(id=h.group(1), title=h.group(2), weight=float(h.group(3)), pts=0.0, earned=0.0)
            wps.append(cur)
            continue
        r = ROW.match(line)
        if r and cur is not None:
            pts, pct = float(r.group(4)), float(r.group(6))
            if not 0 <= pct <= 100:
                problems.append(f"{path.name}:{n}: Done % must be between 0 and 100 (got {pct})")
            cur["pts"] += pts
            cur["earned"] += pts * pct / 100.0
    for w in wps:
        if abs(w["pts"] - w["weight"]) > 1e-9:
            problems.append(f"{path.name}: {w['id']} heading says {fmt(w['weight'])} pts but subtasks add up to {fmt(w['pts'])}")
    return wps, problems


def summary_block(wps):
    total = sum(w["pts"] for w in wps)
    earned = sum(w["earned"] for w in wps)
    pct = 100.0 * earned / total if total else 0.0
    lines = [f"**Overall: {fmt(earned)} / {fmt(total)} points · {pct:.0f}% complete**", "",
             "| WP | Title | Weight | Earned | WP % |", "|---|---|---:|---:|---:|"]
    for w in wps:
        wp_pct = 100.0 * w["earned"] / w["pts"] if w["pts"] else 0.0
        lines.append(f"| {w['id']} | {w['title']} | {fmt(w['pts'])} | {fmt(w['earned'])} | {wp_pct:.0f}% |")
    return "\n".join(lines), earned, total


def replace_block(text, start, end, new):
    pat = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pat.search(text):
        raise SystemExit(f"Marker {start} not found")
    return pat.sub(lambda _m: f"{start}\n{new}\n{end}", text)


def main():
    write = "--write" in sys.argv or "--rollup" in sys.argv
    rollup = "--rollup" in sys.argv
    all_problems, rows, team_earned = [], [], 0.0
    for fname, name in MEMBERS:
        path = TEAM / fname
        if not path.exists():
            all_problems.append(f"Missing file: {path}")
            continue
        wps, problems = parse(path)
        all_problems += problems
        block, earned, total = summary_block(wps)
        team_earned += earned
        rows.append((name, total, earned))
        pct = 100.0 * earned / total if total else 0.0
        print(f"{name:14s} {fmt(earned):>5s} / {fmt(total):<4s} pts  {pct:5.1f}%")
        if write:
            text = path.read_text(encoding="utf-8")
            new = replace_block(text, "<!-- PROGRESS:START -->", "<!-- PROGRESS:END -->", block)
            if new != text:
                path.write_text(new, encoding="utf-8")
                print(f"  updated {fname}")
    print(f"{'TEAM':14s} {fmt(team_earned):>5s} / {fmt(TEAM_TOTAL):<4s} pts  {100 * team_earned / TEAM_TOTAL:5.1f}%")
    if rollup:
        lines = ["| Member | Share (pts) | Earned | Member % |", "|---|---:|---:|---:|"]
        for name, total, earned in rows:
            lines.append(f"| {name} | {fmt(total)} | {fmt(earned)} | {100 * earned / total if total else 0:.0f}% |")
        lines.append(f"| **Team** | **{fmt(TEAM_TOTAL)}** | **{fmt(team_earned)}** | **{100 * team_earned / TEAM_TOTAL:.0f}%** |")
        readme = TEAM / "README.md"
        text = readme.read_text(encoding="utf-8")
        new = replace_block(text, "<!-- ROLLUP:START -->", "<!-- ROLLUP:END -->", "\n".join(lines))
        if new != text:
            readme.write_text(new, encoding="utf-8")
            print("  updated docs/team/README.md")
    if all_problems:
        print("\nPROBLEMS:")
        for p in all_problems:
            print(" -", p)
        sys.exit(1)


if __name__ == "__main__":
    main()
