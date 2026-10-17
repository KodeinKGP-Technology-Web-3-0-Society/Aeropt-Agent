#!/usr/bin/env python3
"""Fetch PYQ papers and questions from jeechallenger.com.

Dumps papers and question chunks into .local-run/jeechallenger/ for use
in Aeropt's local retrieval index, content bank, and practice solver.

Usage:
    python -m local_ingest.exams.fetch_jeechallenger --all
    python -m local_ingest.exams.fetch_jeechallenger --subject Physics
    python -m local_ingest.exams.fetch_jeechallenger --papers-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = "https://www.jeechallenger.com"

# Default session cookie (can be overridden via JEE_CHALLENGER_COOKIE environment variable)
DEFAULT_COOKIE = (
    "__Host-next-auth.csrf-token=044f1e8c6f5b73e2c2a41de91e963e8bcce0ef48681663f963f836318f1cd9d6%7C0c7447b119299fede6500be0d21f9c32d558573ae99859f12a44ca96c9dd04ec; "
    "__Secure-next-auth.callback-url=https%3A%2F%2Fwww.jeechallenger.com%2Fai-tutor; "
    "__Secure-next-auth.session-token=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..ygYMqhFlDOnPzVtv.ht0gppYhfs6er4LvYWSbGtL-1RLgBgqYrUWoCydk4201NqQ9TdWmUWjYGBvprRrdcPESBiPcPOaEa9wXw-GRROBuLeX916uRiPPYfVzlyI3rYIZi1PMmz2SQJ9YZRcb2g_ZYQTxu9WdAljqA_15c_I3ivhI5nrhZpBVZB5nNB2FFoEyUrss21W9AYFoX4c-Oxov04ZS1copIBNpoBbExqzjiej_WE6kHGJBD1WQjQLxPm8cdGUPCuYWqQh6I77EzeeBn44QlEOXUMihub5Q9rb979mkzTV5M3JYHJsihbHSnSoF_iSk10cZEIti3QOK-24iAq1A6T5okQnvTl2lJCHR9Nk_HIZdmijkoLurX0kSHuP6n1pC2GbiHn_TvicuA54E7Gx5MfZc5sd9QAvX-xqch1uBQsznv8hLJO0L-NerYy9ypk_8QEJh9hPD8nLcKITeqlGj7lMl-fdB-JKWwjBQ65VefKwkyXq_xJqtOTYsijz-yiXVoRZs_mn8e83jp8-HoV8U301OyRMte4kfcaMXN1TJTE8aE68NEYXqnEysLE_vgps0hWcVOk0tVenNPKM1AHL2HtSR5TACie8RK9jcDXTaAepf2ibTHGn8YOD_7mXTGXoLLcpEp8uPZuVCAvT2F5UGuoSj6NY52nwkY7d8TgYbzJTZGfN2KUvMrnwNRVjYhMhpaVrz5wUJEOiXjgI0C1WR28C0YITU0vwNFri82L2KkgNimXrho9OUKGSzZ0Tgn3wEWX8Gcmh9DNuAjiOal8r6m7h4W1p3PDw-51cpWXA18QPz-REBOAHUBruOslwV99ty1YVBW.7C4to17k2qpyjUuF4LYoYA"
)


def _get_headers(cookie: str | None = None) -> dict[str, str]:
    token = cookie or os.environ.get("JEE_CHALLENGER_COOKIE") or DEFAULT_COOKIE
    return {
        "accept": "*/*",
        "cookie": token,
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    }


def fetch_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_papers(headers: dict[str, str], out_dir: Path) -> list[dict[str, Any]]:
    print("Fetching papers index...")
    all_papers: list[dict[str, Any]] = []
    page = 1
    limit = 50

    while True:
        url = f"{BASE_URL}/api/pyqs/papers?page={page}&limit={limit}"
        data = fetch_json(url, headers)
        items = data.get("data", [])
        if not items:
            break
        all_papers.extend(items)
        meta = data.get("meta", {})
        total_pages = meta.get("total_pages", 1)
        print(f"  Papers page {page}/{total_pages} (collected {len(all_papers)} papers)")
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.5)

    out_file = out_dir / "papers.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_papers, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(all_papers)} papers to {out_file}")
    return all_papers


def fetch_questions(
    headers: dict[str, str],
    out_dir: Path,
    subject: str | None = None,
    exam_type: str | None = None,
    exam_year: int | None = None,
    chapter: str | None = None,
) -> list[dict[str, Any]]:
    print("Fetching questions...")
    all_questions: list[dict[str, Any]] = []
    page = 1
    limit = 100

    query_params: dict[str, Any] = {"limit": limit}
    if subject:
        query_params["subject"] = subject
    if exam_type:
        query_params["exam_type"] = exam_type
    if exam_year:
        query_params["exam_year"] = exam_year
    if chapter:
        query_params["chapter"] = chapter

    while True:
        query_params["page"] = page
        qs = urllib.parse.urlencode(query_params)
        url = f"{BASE_URL}/api/pyqs/questions?{qs}"
        try:
            data = fetch_json(url, headers)
        except Exception as e:
            print(f"Error fetching page {page}: {e}", file=sys.stderr)
            break

        items = data.get("data", [])
        if not items:
            break
        all_questions.extend(items)
        meta = data.get("meta", {})
        total_pages = meta.get("total_pages", 1)
        total_items = meta.get("total", len(all_questions))
        print(f"  Questions page {page}/{total_pages} ({len(all_questions)}/{total_items})")
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.5)

    suffix = ""
    if subject:
        suffix += f"_{subject.lower()}"
    if exam_type:
        suffix += f"_{exam_type.lower()}"
    if exam_year:
        suffix += f"_{exam_year}"

    raw_file = out_dir / f"questions{suffix}.json"
    with open(raw_file, "w", encoding="utf-8") as f:
        json.dump(all_questions, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(all_questions)} questions to {raw_file}")

    # Also save as jsonl for easy streaming / indexing
    jsonl_file = out_dir / f"questions{suffix}.jsonl"
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for q in all_questions:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"Saved JSONL format to {jsonl_file}")

    return all_questions


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch PYQ papers and questions from jeechallenger.com")
    parser.add_argument("--all", action="store_true", help="Fetch both papers and all questions")
    parser.add_argument("--papers-only", action="store_true", help="Fetch only papers catalog")
    parser.add_argument("--questions-only", action="store_true", help="Fetch questions only")
    parser.add_argument("--subject", type=str, choices=["Physics", "Chemistry", "Mathematics"], help="Filter by subject")
    parser.add_argument("--exam-type", type=str, choices=["JEE_MAIN", "JEE_ADVANCED"], help="Filter by exam type")
    parser.add_argument("--year", type=int, help="Filter by year (e.g. 2025, 2026)")
    parser.add_argument("--chapter", type=str, help="Filter by chapter name")
    parser.add_argument("--cookie", type=str, help="Custom NextAuth cookie string")
    parser.add_argument("--out-dir", type=str, default=".local-run/jeechallenger", help="Output directory")

    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    headers = _get_headers(args.cookie)

    # Check session
    try:
        session = fetch_json(f"{BASE_URL}/api/auth/session", headers)
        user = session.get("user", {})
        print(f"Authenticated as: {user.get('email')} (expires: {session.get('expires')})")
    except Exception as e:
        print(f"Warning: Session check returned {e}. Cookies may be expired.")

    if args.papers_only or args.all or (not args.questions_only and not args.subject):
        fetch_papers(headers, out_dir)

    if args.questions_only or args.all or args.subject or args.exam_type or args.year:
        fetch_questions(
            headers=headers,
            out_dir=out_dir,
            subject=args.subject,
            exam_type=args.exam_type,
            exam_year=args.year,
            chapter=args.chapter,
        )


if __name__ == "__main__":
    main()
