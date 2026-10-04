"""Official JEE Advanced papers and their official final answer keys.

Scope data, like jee_syllabus.py: where each official document lives, nothing more.
Question papers come from the jeeadv.ac.in archive. Final keys are only published
for the current cycle, so earlier ones are fetched from the Internet Archive's
capture of the official URL (`wayback` timestamp) — the bytes are the official file.

Each key document repeats the full paper with "Answer: …" under every question, so
it doubles as a cross-check: the key's copy of a question must match the stem
extracted from the clean paper before the two are joined.

Pin `key_wayback` to an exact capture (from the CDX index), not a date: the same URL
was later overwritten — 2025's p1_solutions_final.pdf became a 9-page Mathematics-only
file on 14 June 2025, and "closest capture" resolution handed that back. The join then
refused it (no key block matched), which is the safeguard working, not a fix.

Adding a year: add a Paper row; `python -m local_ingest.exams --years Y` does the rest.
Years not listed: 2007-2013, 2018, 2020 and 2021 have no official key located; 2022's
final key is one document for both papers, and answers keyed only by question number would
mix them, so it waits for a page-range split; their questions are not ingested, because an unkeyed question cannot be graded.
"""

from __future__ import annotations

from dataclasses import dataclass

EXAM = "JEE Advanced"
SUBJECT = "Physics"


@dataclass(frozen=True)
class Paper:
    year: int
    paper: int
    paper_url: str
    key_url: str
    key_wayback: str = ""  # Internet Archive timestamp when the key is no longer live

    @property
    def slug(self) -> str:
        return f"jee_adv_{self.year}_p{self.paper}"

    @property
    def key_fetch_url(self) -> str:
        if self.key_wayback:
            return f"https://web.archive.org/web/{self.key_wayback}id_/{self.key_url}"
        return self.key_url


_ARCHIVE = "https://jeeadv.ac.in/past_qps"
_DOCS = "https://jeeadv.ac.in/documents"

PAPERS: list[Paper] = [
    Paper(2026, 1, f"{_DOCS}/p1_english.pdf", f"{_DOCS}/p1_solutions_final.pdf", "20260601000000"),
    Paper(2026, 2, f"{_DOCS}/p2_english.pdf", f"{_DOCS}/p2_solutions_final.pdf", "20260601000000"),
    Paper(2025, 1, f"{_ARCHIVE}/2025_1_English.pdf", f"{_DOCS}/p1_solutions_final.pdf", "20250602022251"),
    Paper(2025, 2, f"{_ARCHIVE}/2025_2_English.pdf", f"{_DOCS}/p2_solutions_final.pdf", "20250602022251"),
    Paper(2024, 1, f"{_ARCHIVE}/2024_1_English.pdf", f"{_DOCS}/Paper1_English_2024_With_Final_Keys.pdf", "20240609000000"),
    Paper(2024, 2, f"{_ARCHIVE}/2024_2_English.pdf", f"{_DOCS}/Paper2_English_2024_With_Final_Keys.pdf", "20240609000000"),
    Paper(2023, 1, f"{_ARCHIVE}/2023_1_English.pdf", f"{_DOCS}/Paper1_Final_Answer_Keys.pdf", "20230618000000"),
    Paper(2023, 2, f"{_ARCHIVE}/2023_2_English.pdf", f"{_DOCS}/Paper2_Final_Answer_Keys.pdf", "20230618000000"),
    # 2019: scanned papers (subjects found from page thumbnails); the key is a subject table.
    Paper(2019, 1, f"{_ARCHIVE}/2019_1_English.pdf", f"{_DOCS}/KEY-2019-Final-13.06.2019-P1.pdf", "20190713000000"),
    Paper(2019, 2, f"{_ARCHIVE}/2019_2_English.pdf", f"{_DOCS}/KEY-2019-Final-13.06.2019-P2.pdf", "20190713000000"),
    # 2017: numbered 1-54 straight through; "Ans for Q.n:" keys. Paper 1's key was revised.
    Paper(2017, 1, f"{_ARCHIVE}/2017_1.pdf", "https://jeeadv.ac.in/pdf/Revised_P1_2017.pdf", "20170813000000"),
    Paper(2017, 2, f"{_ARCHIVE}/2017_2.pdf", "http://jeeadv.ac.in/pdf/P2_2017.pdf", "20170609000000"),
    # 2015, 2016: scanned papers and scanned keys (the key is the paper with an answer box
    # under each question); both read from page images, keys by exams/keys_vision.py.
    Paper(2016, 1, f"{_ARCHIVE}/2016_1.pdf", "http://www.jeeadv.ac.in:80/pdf/2016p1key.pdf", "20160615000000"),
    Paper(2016, 2, f"{_ARCHIVE}/2016_2.pdf", "http://www.jeeadv.ac.in:80/pdf/2016p2key.pdf", "20160615000000"),
    Paper(2015, 1, f"{_ARCHIVE}/2015_1.pdf",
          "http://jeeadv.iitb.ac.in:80/sites/default/files/JEE-Adv-2015_P1_Final-Key-Official-13-06-2015.pdf", "20150615000000"),
    Paper(2015, 2, f"{_ARCHIVE}/2015_2.pdf",
          "http://jeeadv.iitb.ac.in:80/sites/default/files/JEE-Adv-2015_P2_Final-Key-Official-13-06-2015.pdf", "20150616000000"),
    # 2014: the official "Questions with Answers" documents (answers printed in the paper).
    Paper(2014, 1, f"{_ARCHIVE}/2014_1.pdf", "http://jeeadv.iitkgp.ac.in/docs/2014p1key.pdf", "20140611000000"),
    Paper(2014, 2, f"{_ARCHIVE}/2014_2.pdf", "http://jeeadv.iitkgp.ac.in:80/docs/2014p2key.pdf", "20140630000000"),
]


def select(years: set[int] | None = None) -> list[Paper]:
    return [p for p in PAPERS if not years or p.year in years]
