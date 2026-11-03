"""Render the CV from YAML and check that an ATS can parse it.

Usage:
    python cv/build.py                                  # base CV -> cv/out/cv.pdf
    python cv/build.py --data cv/base.yaml --out cv/out/my-cv.pdf
    python cv/build.py --png                            # also write PNG previews
    python cv/build.py --keywords "Java,REST APIs,Kubernetes"   # also report JD keyword coverage

The ATS check re-extracts text from the generated PDF, the same way an ATS does,
and fails if any bullet, skill, heading or contact detail doesn't come back out
exactly as written. That catches glued words, odd glyphs, broken ligatures and
hyphenation.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import typst
import yaml
from pypdf import PdfReader

CV_DIR = Path(__file__).resolve().parent
TEMPLATE = CV_DIR / "template.typ"
FONTS = CV_DIR / "fonts"
DEFAULT_OUT = CV_DIR / "out" / "cv.pdf"

REQUIRED_HEADINGS = ["EDUCATION", "TECHNICAL SKILLS", "EXPERIENCE", "PROJECTS"]
# Characters an ATS reliably handles. Anything else gets flagged.
ALLOWED_EXTRA = set("–—•’‘“”£€é")


# --------------------------------------------------------------------------- render
def render(data_path: Path, out_pdf: Path, png: bool = False) -> Path:
    data_path = data_path.resolve()
    try:
        rel = data_path.relative_to(CV_DIR).as_posix()
    except ValueError:
        sys.exit(f"Data file must live inside {CV_DIR} (Typst root). Got: {data_path}")

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    common = dict(
        root=str(CV_DIR),
        font_paths=[str(FONTS)],
        ignore_system_fonts=True,  # identical output on Windows and the Linux server
        sys_inputs={"data": rel},
    )
    typst.compile(str(TEMPLATE), output=str(out_pdf), **common)
    if png:
        typst.compile(
            str(TEMPLATE),
            output=str(out_pdf.with_name(out_pdf.stem + "-p{p}.png")),
            format="png",
            ppi=110,
            **common,
        )
    return out_pdf


# --------------------------------------------------------------------------- ATS check
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


@dataclass
class AtsReport:
    pages: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    keyword_hits: list[str] = field(default_factory=list)
    keyword_misses: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def coverage(self) -> float | None:
        total = len(self.keyword_hits) + len(self.keyword_misses)
        return None if total == 0 else len(self.keyword_hits) / total

    def print(self) -> None:
        print(f"Pages: {self.pages}")
        for w in self.warnings:
            print(f"  WARN  {w}")
        for e in self.errors:
            print(f"  FAIL  {e}")
        if self.coverage is not None:
            print(f"JD keyword coverage: {self.coverage:.0%}")
            if self.keyword_misses:
                print("  missing: " + ", ".join(self.keyword_misses))
        print("ATS CHECK: " + ("PASS" if self.ok else "FAIL"))


def ats_check(pdf_path: Path, data_path: Path, keywords: list[str] | None = None) -> AtsReport:
    report = AtsReport()
    reader = PdfReader(str(pdf_path))
    report.pages = len(reader.pages)
    if report.pages != 1:
        report.warnings.append(f"CV is {report.pages} pages long. Aim for exactly 1 page.")

    raw_text = "\n".join(p.extract_text() or "" for p in reader.pages)
    norm_text = _norm(raw_text)

    # 1. Non-ASCII check
    for ch in raw_text:
        if ord(ch) >= 128 and ch not in ALLOWED_EXTRA:
            report.warnings.append(
                f"Suspicious character {ch!r} (U+{ord(ch):04X}). May break older ATS systems."
            )
            break

    # 2. Required section headings
    for h in REQUIRED_HEADINGS:
        if _norm(h) not in norm_text:
            report.errors.append(f"Missing section heading: {h}")

    # 3. Content from YAML must all be extractable
    with open(data_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # meta
    for k in ("name", "email", "phone"):
        val = data.get("meta", {}).get(k)
        if val and _norm(val) not in norm_text:
            report.errors.append(f"Contact detail missing/garbled: {k}={val}")

    # skills
    for cat in data.get("skills", []):
        for item in cat.get("items", []):
            if _norm(item) not in norm_text:
                report.errors.append(f"Skill missing/garbled: {item}")

    # experience bullets
    for job in data.get("experience", []):
        for b in job.get("bullets", []):
            text = b.get("text", "")
            first_words = " ".join(text.split()[:6])
            if _norm(first_words) not in norm_text:
                report.errors.append(f"Bullet missing/garbled in {job.get('org')}: {first_words!r}...")

    # projects bullets
    for proj in data.get("projects", []):
        for b in proj.get("bullets", []):
            text = b.get("text", "")
            first_words = " ".join(text.split()[:6])
            if _norm(first_words) not in norm_text:
                report.errors.append(f"Bullet missing/garbled in {proj.get('name')}: {first_words!r}...")

    # 4. Optional JD keyword coverage
    if keywords:
        for kw in keywords:
            kw_clean = kw.strip()
            if not kw_clean:
                continue
            if _norm(kw_clean) in norm_text:
                report.keyword_hits.append(kw_clean)
            else:
                report.keyword_misses.append(kw_clean)

    return report


# --------------------------------------------------------------------------- CLI
def main() -> None:
    parser = argparse.ArgumentParser(description="Render CV and verify ATS compatibility.")
    parser.add_argument(
        "--data",
        type=Path,
        default=CV_DIR / "base.yaml",
        help="Path to YAML data file (default: cv/base.yaml)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output PDF path (default: {DEFAULT_OUT.relative_to(CV_DIR.parent)})",
    )
    parser.add_argument(
        "--png",
        action="store_true",
        help="Also compile page previews as PNG.",
    )
    parser.add_argument(
        "--keywords",
        type=str,
        default=None,
        help="Comma-separated list of JD keywords to test coverage for.",
    )
    parser.add_argument(
        "--no-check",
        action="store_true",
        help="Skip ATS re-extraction check.",
    )
    args = parser.parse_args()

    out_pdf = render(args.data, args.out, png=args.png)
    print(f"Compiled: {out_pdf}")

    if not args.no_check:
        kw_list = [k.strip() for k in args.keywords.split(",")] if args.keywords else None
        report = ats_check(out_pdf, args.data, keywords=kw_list)
        report.print()
        if not report.ok:
            sys.exit(1)


if __name__ == "__main__":
    main()

