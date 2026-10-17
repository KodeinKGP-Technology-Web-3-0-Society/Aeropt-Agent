# JEE Challenger API Reference & Extraction Guide

This document preserves the API endpoints, query parameters, authentication headers, and data schemas for **jeechallenger.com**, allowing future extraction of JEE Main and JEE Advanced PYQ (Previous Year Questions) datasets, solutions, and chunks for Aeropt's retrieval corpus and practice generator.

---

## 1. Quick Summary of Corpus Available

- **Total Questions:** 2,311 verified questions (Mathematics, Physics, Chemistry)
- **Total Papers:** 39 full exam papers (JEE Advanced & JEE Main 2012–2026)
- **Features per Question:**
  - Standard KaTeX LaTeX formatting (`$...$`, `$$...$$`) for stems, options, and solutions
  - Full step-by-step explanatory solutions
  - Canonical answer keys (`correct_answer`, `numeric_answer`)
  - Classification metadata: `subject`, `chapter`, `tags`, `difficulty`, `question_type` (MCQ, NUMERIC, etc.)
  - Paper provenance: `exam_year`, `exam_type` (`JEE_MAIN`, `JEE_ADVANCED`), `original_paper_id`, `question_number`
  - High-resolution diagram asset URLs when applicable

---

## 2. Base URL & Authentication

- **Base URL:** `https://www.jeechallenger.com`
- **Auth Strategy:** Cookie-based session via NextAuth (`__Secure-next-auth.session-token`).
- **Required Cookie Header:**
  ```http
  Cookie: __Host-next-auth.csrf-token=044f1e8c6f5b73e2c2a41de91e963e8bcce0ef48681663f963f836318f1cd9d6%7C0c7447b119299fede6500be0d21f9c32d558573ae99859f12a44ca96c9dd04ec; __Secure-next-auth.callback-url=https%3A%2F%2Fwww.jeechallenger.com%2Fai-tutor; __Secure-next-auth.session-token=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..ygYMqhFlDOnPzVtv.ht0gppYhfs6er4LvYWSbGtL-1RLgBgqYrUWoCydk4201NqQ9TdWmUWjYGBvprRrdcPESBiPcPOaEa9wXw-GRROBuLeX916uRiPPYfVzlyI3rYIZi1PMmz2SQJ9YZRcb2g_ZYQTxu9WdAljqA_15c_I3ivhI5nrhZpBVZB5nNB2FFoEyUrss21W9AYFoX4c-Oxov04ZS1copIBNpoBbExqzjiej_WE6kHGJBD1WQjQLxPm8cdGUPCuYWqQh6I77EzeeBn44QlEOXUMihub5Q9rb979mkzTV5M3JYHJsihbHSnSoF_iSk10cZEIti3QOK-24iAq1A6T5okQnvTl2lJCHR9Nk_HIZdmijkoLurX0kSHuP6n1pC2GbiHn_TvicuA54E7Gx5MfZc5sd9QAvX-xqch1uBQsznv8hLJO0L-NerYy9ypk_8QEJh9hPD8nLcKITeqlGj7lMl-fdB-JKWwjBQ65VefKwkyXq_xJqtOTYsijz-yiXVoRZs_mn8e83jp8-HoV8U301OyRMte4kfcaMXN1TJTE8aE68NEYXqnEysLE_vgps0hWcVOk0tVenNPKM1AHL2HtSR5TACie8RK9jcDXTaAepf2ibTHGn8YOD_7mXTGXoLLcpEp8uPZuVCAvT2F5UGuoSj6NY52nwkY7d8TgYbzJTZGfN2KUvMrnwNRVjYhMhpaVrz5wUJEOiXjgI0C1WR28C0YITU0vwNFri82L2KkgNimXrho9OUKGSzZ0Tgn3wEWX8Gcmh9DNuAjiOal8r6m7h4W1p3PDw-51cpWXA18QPz-REBOAHUBruOslwV99ty1YVBW.7C4to17k2qpyjUuF4LYoYA
  ```

---

## 3. Endpoints

### 3.1. Session Check
`GET https://www.jeechallenger.com/api/auth/session`

- **Purpose:** Verifies whether the NextAuth session token is valid and returns user subscription limits.
- **Example cURL:**
  ```bash
  curl -s 'https://www.jeechallenger.com/api/auth/session' \
    -H 'accept: */*' \
    -b '__Secure-next-auth.session-token=...'
  ```

### 3.2. Available Exam Years
`GET https://www.jeechallenger.com/api/pyqs/papers/years`

- **Purpose:** Returns the list of available exam years (e.g. `[2026, 2025, 2024, ..., 2012]`).
- **Response Structure:**
  ```json
  {
    "data": [2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017, 2016, 2015, 2014, 2013, 2012],
    "total": 15
  }
  ```

### 3.3. Full Papers & Section Structure
`GET https://www.jeechallenger.com/api/pyqs/papers?page=1&limit=50`

- **Purpose:** Lists full papers with their sections, marking schemes, question order, and question IDs.
- **Query Parameters:**
  - `page`: Page number (1-indexed).
  - `limit`: Number of papers per page (e.g. `50`).
- **Response Structure:**
  ```json
  {
    "data": [
      {
        "_id": "6a9149276c0fe1d1ecff1659",
        "title": "JEE Advanced 2026 - Paper 1",
        "exam_type": "JEE_ADVANCED",
        "exam_year": 2026,
        "total_questions": 51,
        "total_marks": 180,
        "duration_minutes": 180,
        "sections": [
          {
            "name": "Physics Section 1",
            "subject": "Physics",
            "section_type": "MCQ",
            "total_questions": 4,
            "section_marks": 12,
            "marking_scheme": {
              "algorithm": "SINGLE_CORRECT",
              "correct": 3,
              "incorrect": -1
            },
            "question_ids": ["6a9149276c0fe1d1ecff165a", ...]
          }
        ]
      }
    ],
    "meta": { "page": 1, "limit": 50, "total": 39, "total_pages": 1 }
  }
  ```

### 3.4. Question Bank (PYQs)
`GET https://www.jeechallenger.com/api/pyqs/questions`

- **Purpose:** Retrieves questions with full text, formulas, options, correct answers, and step-by-step solutions.
- **Query Parameters:**
  - `page`: Page number (1-indexed).
  - `limit`: Number of questions per page (supports up to `100`).
  - `subject`: Filter by subject (`Physics`, `Chemistry`, `Mathematics`).
  - `chapter`: Filter by chapter name (URL-encoded, e.g. `Thermodynamics`, `Conic Sections`).
  - `exam_type`: Filter by exam (`JEE_MAIN`, `JEE_ADVANCED`).
  - `exam_year`: Filter by year (e.g. `2025`, `2026`).
  - `difficulty`: Filter by difficulty (`Easy`, `Medium`, `Hard`).
- **Example cURL:**
  ```bash
  curl -s 'https://www.jeechallenger.com/api/pyqs/questions?page=1&limit=100&subject=Physics' \
    -H 'accept: */*' \
    -b '__Secure-next-auth.session-token=...'
  ```
- **Response Structure:**
  ```json
  {
    "data": [
      {
        "_id": "6a8174a850c0eddd0485ea30",
        "question_number": 2,
        "subject": "Mathematics",
        "chapter": "Conic Sections",
        "tags": ["Parabola", "Tangents and Normals"],
        "difficulty": "Medium",
        "question_type": "MCQ",
        "input_format": "MCQ",
        "question_text": "Let $T$ be the tangent to the parabola $y^2 = 16x$...",
        "options": {
          "A": { "text": "$\\frac{15}{4}$", "diagram_urls": [] },
          "B": { "text": "$4$", "diagram_urls": [] },
          "C": { "text": "$\\frac{17}{4}$", "diagram_urls": [] },
          "D": { "text": "$5$", "diagram_urls": [] }
        },
        "correct_answer": ["C"],
        "numeric_answer": null,
        "solution": "To find the distance between the point...",
        "question_diagram_urls": [],
        "original_paper_id": "JEE_Advanced_2026_Paper_2",
        "exam_year": 2026,
        "exam_type": "JEE_ADVANCED"
      }
    ],
    "meta": {
      "limit": 100,
      "page": 1,
      "total": 2311,
      "total_pages": 24
    }
  }
  ```

---

## 4. Extraction Utility

An automated batch extractor is provided at:
`Aeropt-agent/local_ingest/exams/fetch_jeechallenger.py`

### Usage:
```bash
# Dump all 2,311 questions and 39 papers into .local-run/jeechallenger/
python -m local_ingest.exams.fetch_jeechallenger --all

# Fetch only Physics questions
python -m local_ingest.exams.fetch_jeechallenger --subject Physics

# Fetch only JEE Main 2025 questions
python -m local_ingest.exams.fetch_jeechallenger --exam-type JEE_MAIN --year 2025
```
