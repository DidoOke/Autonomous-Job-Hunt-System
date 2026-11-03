"""Scoring and Tiering Pipeline.

Implements the normalized multi-stage job scoring and filtering architecture:
Normalize -> GATES -> Weighted Scoring -> Tier Assignment.

Architecture Overview:
1. Normalization:
   - Unescape HTML, strip tags, lowercase, replace '&' with ' and '
   - Keep only [a-z0-9+#./\\- ] to preserve tech tokens (e.g. c++, c#, .net, ml/ai)
   - Collapse whitespace
   - Boundary-safe lookarounds: B = (?<![a-z0-9]), E = (?![a-z0-9])

2. GATES (Structural Pass/Fail Filters):
   - Gate 1 (Internship Check):
     Requires internship signal in title, employment_type, or description.
     If only present in description, title must match tech (strong or weak)
     AND description must contain a duration signal (e.g. '12 weeks', 'summer 2025').
     Failure drops immediately with 'gate1:fail' or 'gate1:fail_no_duration'.
   - Gate 2 (Tech Relevance Check):
     Requires TECH_STRONG in title or department.
     Alternatively, TECH_WEAK in title AND corroborating evidence
     (>= 2 TECH_DESC keywords or DEGREE match in description).
     Failure drops immediately with 'gate2:fail'.
     Overridden by company_rule.force_tech.

3. Weighted Scoring:
   Positive Signals:
   - Internship location: title (+10), employment_type (+8), description (+4)
   - TECH_STRONG in title: +8 each (capped at +16)
   - TECH_WEAK in title: +3 each (capped at +6)
   - TECH_DESC in description: +1 each (capped at +6)
   - Degree match: +4
   - Tech department: +6
   - Company tier bonus: Tier A (+10), Tier B (+6), Tier C (+3)
   - Freshness bonus: <= 1 day (+10), <= 7 days (+6), <= 30 days (+2)
   - Location bonus: Manchester (+15), London (+12), UK other (+6)

   Penalties & Deductions:
   - Non-UK (when not remote_ambiguous): drop immediately (score 0, tier 'drop', 'loc:non_uk')
   - Remote ambiguous (bare remote): -5 ('loc:remote_ambiguous')
   - NEG_TITLE (non-tech/irrelevant role): -14 per unique occurrence
   - DATA_ENTRY in title: -20
   - IT_SUPPORT in title or description: -12
   - SENIOR in title: -12
   - FULLTIME in title (without intern in title): -8
   - NEG_DESC in description: -2 each (capped at -6)

4. Overrides:
   - is_test == True: -100 penalty, forced tier 'drop'
   - CompanyRule.force_tech == True: bypass Gate 2
   - CompanyRule.force_drop == True: return score 0, tier 'drop', 'override:force_drop'
   - Recruiting suffix stripping: removes team suffixes (e.g. 'Talent Acquisition') from title

5. Tiers (Score Cutoffs):
   - High:   score >= 25
   - Medium: 15 <= score < 25
   - Low:    8 <= score < 15
   - Drop:   score < 8 (or is_test / force_drop)
   Score capped at 100.
"""

import html
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Lookarounds safe for c++, c#, .net, etc.
B = r"(?<![a-z0-9])"
E = r"(?![a-z0-9])"


def normalize(text: Optional[str]) -> str:
    """Normalize input text:
    - html.unescape
    - strip HTML tags (<[^>]+>)
    - lowercase
    - replace & with ' and '
    - keep only [a-z0-9+#./\\- ] (preserve c++, c#, .net, ml/ai)
    - collapse whitespace
    """
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9+#./\- ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Rule library
INTERN = re.compile(
    rf"{B}(?:"
    r"internships?|interns?|co-?ops?|"
    r"summer\s+(?:analysts?|associates?|placements?)|"
    r"(?:industrial\s+|summer\s+)?placements?|placement\s+years?|"
    r"student\s+engineers?|working\s+students?|werkstudents?|"
    r"trainees?|apprenticeships?|apprentices?"
    rf"){E}",
    re.I,
)

TECH_STRONG = re.compile(
    rf"{B}(?:"
    r"applied\s+scientists?|research\s+scientists?|research\s+(?:engineers?|scientists?)|"
    r"quant(?:itative)?\s+(?:developers?|research(?:ers?)?|analysts?)|"
    r"machine\s+learning|deep\s+learning|"
    r"data\s+(?:science|scientist|engineering|engineer)s?|"
    r"cloud\s+engineers?|platform\s+engineers?|"
    r"cyber\s*security|infosec|security|"
    r"quality\s+assurance|qa|"
    r"test\s+(?:engineers?|engineering|automation)|"
    r"game\s+developers?|"
    r"full-?stack|backend|frontend|"
    r"software|swe|sde|sdet|"
    r"mobile|ios|android|"
    r"devops|sre|"
    r"ml|ai|nlp|llm|genai|"
    r"computer\s+science|"
    r"infrastructure|embedded|firmware|"
    r"graphics|robotics|"
    r"developers?|programmers?|blockchain"
    rf"){E}",
    re.I,
)

TECH_WEAK = re.compile(
    rf"{B}(?:"
    r"information\s+technology|business\s+intelligence|"
    r"engineers?|engineering|technical|technology|tech|"
    r"data|analysts?|analytics|scientists?|research(?:er)?|"
    r"computers?|computing|quantitative|math|mathematics|statistics|physics"
    rf"){E}",
    re.I,
)

TECH_DESC = re.compile(
    rf"{B}(?:"
    r"machine\s+learning|deep\s+learning|neural\s+networks?|"
    r"computer\s+science|software\s+(?:development|engineering)|programming|"
    r"data\s+structures?|operating\s+systems?|compilers?|algorithms?|"
    r"microservices|graphql|rest|apis?|"
    r"javascript|typescript|python|java|c\+\+|c#|\.net|golang|rust|ruby|php|swift|kotlin|scala|"
    r"postgres(?:ql)?|mysql|mongodb|redis|sql|"
    r"react(?:\.js)?|angular|vue(?:\.js)?|node(?:\.js)?|django|flask|spring|rails|"
    r"pytorch|tensorflow|scikit-learn|pandas|numpy|"
    r"docker|kubernetes|terraform|git|linux|"
    r"aws|gcp|azure"
    rf"){E}",
    re.I,
)

DEGREE = re.compile(
    rf"{B}(?:pursuing|working\s+towards|enrolled|bachelors?|masters?|phd|bs|ms){E}"
    r"[\s\S]{0,120}?"
    rf"{B}(?:computer\s+science|software\s+engineering|computer\s+engineering|"
    r"electrical\s+engineering|information\s+technology|data\s+science|"
    rf"mathematics|statistics|related\s+field){E}",
    re.I,
)

NEG_TITLE = re.compile(
    rf"{B}(?:"
    r"business\s+(?:development|developer(?:s)?|analysts?)|"
    r"go\s+to\s+market|"
    r"investment\s+banking|"
    r"human\s+resources|talent\s+acquisition|people\s+operations|"
    r"public\s+relations|social\s+media|"
    r"graphic\s+design|ui\s+design|ux\s+design|visual\s+design|product\s+design|interior\s+design|"
    r"customer\s+(?:success|support|service)|client\s+services|call\s+center|"
    r"office\s+assistants?|data\s+entry|"
    r"supply\s+chain|project\s+managers?|program\s+managers?|"
    r"industrial\s+engineering|electrical\s+engineering|"
    r"lab\s+assistants?|events\s+planning|food\s+service|"
    r"solutions\s+engineers?|sales\s+engineers?|sales\s+development|field\s+engineers?|"
    r"real\s+estate|"
    r"marketing|sales|finance|financial|accounting|audit|tax|treasury|"
    r"hr|recruiting|recruitment|legal|law|compliance|regulatory|"
    r"communications?|pr|content|editorial|copywriting|"
    r"administrative|admin|logistics|procurement|manufacturing|"
    r"mechanical|civil|chemical|nursing|clinical|biology|chemistry|biochemistry|"
    r"hospitality|technicians?|property|insurance|underwriting"
    rf"){E}",
    re.I,
)

NEG_DESC = re.compile(
    rf"{B}(?:"
    r"cold\s+calls?|data\s+entry|help\s*desk|desktop\s+support|call\s+center|"
    r"sales|marketing|quotas?|commissions?"
    rf"){E}",
    re.I,
)

SENIOR = re.compile(
    rf"{B}(?:"
    r"vice\s+president|head\s+of|"
    r"senior|sr\.?|staff|principal|lead|managers?|directors?|architects?|vp|chief"
    rf"){E}",
    re.I,
)

FULLTIME = re.compile(
    rf"{B}(?:"
    r"new\s+grads?|new\s+graduates?|full-?time|experienced|"
    r"(?:[3-9]|\d{2,})\+?\s*(?:years?|yrs?)(?:\s+experience)?"
    rf"){E}",
    re.I,
)

IT_SUPPORT = re.compile(
    rf"{B}(?:"
    r"help\s*desk|it\s+support|desktop\s+support|technical\s+support|service\s+desk"
    rf"){E}",
    re.I,
)

DATA_ENTRY = re.compile(
    rf"{B}data\s+entry{E}",
    re.I,
)

DURATION_RE = re.compile(r"\b\d{1,2}\s*(?:week|month)s?\b|\bsummer\s+20\d\d\b", re.I)

RECRUITING_SUFFIX = re.compile(
    r"(?:"
    r"[\s,|\-\u2013\u2014]+(?:(?:amazon|university|campus|corporate)\s+)*talent\s+acquisition"
    r"|[\s,|\-\u2013\u2014]+(?:(?:amazon|university|campus|corporate)\s+)*recruit(?:ing|ment)\s+team"
    r"|[\s,|\-\u2013\u2014]+(?:human\s+resources|hr)\s+team"
    r"|[\s,|\-\u2013\u2014]+hiring\s+team"
    r"|\s*\(via\s+[^)]+\)"
    r")\s*$",
    re.I,
)


def evaluate(
    title: str,
    desc: str = "",
    department: str = "",
    company_tier: str = "",
    employment_type: str = "",
    posted_at: Optional[datetime] = None,
    first_seen: Optional[datetime] = None,
    is_test: bool = False,
    remote_ambiguous: bool = False,
    company_rule: Optional[Any] = None,
    is_uk: Optional[bool] = None,
    is_manchester: bool = False,
    is_london: bool = False,
    is_remote: bool = False,
    job: Optional[Any] = None,
) -> Dict[str, Any]:
    """Evaluates a job posting through gates, weighted scoring, and tier assignment."""
    if job is not None:
        if is_uk is None:
            is_uk = getattr(job, "is_uk", None)
        if not is_manchester:
            is_manchester = getattr(job, "is_manchester", False)
        if not is_london:
            is_london = getattr(job, "is_london", False)
        if not remote_ambiguous:
            remote_ambiguous = getattr(job, "remote_ambiguous", False)
        if not is_test:
            is_test = getattr(job, "is_test", False)
        if posted_at is None:
            posted_at = getattr(job, "posted_at", None)
        if first_seen is None:
            first_seen = getattr(job, "first_seen", None)

    # Strip recruiting-team suffixes from title
    title = RECRUITING_SUFFIX.sub("", title).strip()

    # Step 1. Normalize all inputs
    t_norm = normalize(title)
    d_norm = normalize(desc)
    dept_norm = normalize(department)
    emp_norm = normalize(employment_type)

    # Step 5 check early: force_drop company override
    if company_rule and getattr(company_rule, "force_drop", False):
        return {"score": 0, "tier": "drop", "matched_rules": ["override:force_drop"]}

    # Step 2. GATE 1 - internship check
    intern_title = bool(INTERN.search(t_norm))
    intern_field = bool(INTERN.search(emp_norm))
    intern_desc = bool(INTERN.search(d_norm))

    if not (intern_title or intern_field):
        if not (intern_desc and (TECH_STRONG.search(t_norm) or TECH_WEAK.search(t_norm))):
            return {"score": 0, "tier": "drop", "matched_rules": ["gate1:fail"]}
        # Require duration signal if only described as intern
        if not DURATION_RE.search(d_norm):
            return {"score": 0, "tier": "drop", "matched_rules": ["gate1:fail_no_duration"]}

    # Step 3. GATE 2 - tech relevance check
    strong = set(m.group(0).lower() for m in TECH_STRONG.finditer(t_norm))
    if re.search(r"\bbusiness\s+developers?\b", t_norm):
        strong.discard("developer")
        strong.discard("developers")
    weak = set(m.group(0).lower() for m in TECH_WEAK.finditer(t_norm))
    dept_tech = bool(TECH_STRONG.search(dept_norm))
    dtech = set(m.group(0).lower() for m in TECH_DESC.finditer(d_norm))
    degree = bool(DEGREE.search(d_norm))

    if company_rule and getattr(company_rule, "force_tech", False):
        strong = strong or {"override"}

    if not strong and not dept_tech:
        if not (weak and (len(dtech) >= 2 or degree)):
            return {"score": 0, "tier": "drop", "matched_rules": ["gate2:fail"]}

    # Step 4. Score
    s = 0
    matched: List[str] = []

    if intern_title:
        s += 10
        matched.append("intern:title")
    elif intern_field:
        s += 8
        matched.append("intern:field")
    else:
        s += 4
        matched.append("intern:desc")

    if strong:
        s += min(len(strong) * 8, 16)
        matched.append(f"tech_strong:{len(strong)}")
    if weak:
        s += min(len(weak) * 3, 6)
        matched.append(f"tech_weak:{len(weak)}")
    if dtech:
        s += min(len(dtech) * 1, 6)
        matched.append(f"tech_desc:{len(dtech)}")
    if degree:
        s += 4
        matched.append("degree")
    if dept_tech:
        s += 6
        matched.append("dept_tech")

    # Company tier bonus
    c_tier = company_tier.upper() if company_tier else ""
    if c_tier == "A":
        s += 10
        matched.append("tier:A")
    elif c_tier == "B":
        s += 6
        matched.append("tier:B")
    elif c_tier == "C":
        s += 3
        matched.append("tier:C")

    # Freshness bonus
    now = datetime.now(timezone.utc)
    dt = posted_at or first_seen
    if dt:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days_old = (now - dt).days
        if days_old <= 1:
            s += 10
            matched.append("fresh:1d")
        elif days_old <= 7:
            s += 6
            matched.append("fresh:7d")
        elif days_old <= 30:
            s += 2
            matched.append("fresh:30d")

    # Location gate & bonus (applied after tech gates pass, before penalty scoring)
    if is_uk is not None:
        if not is_uk:
            if remote_ambiguous:
                s -= 5
                matched.append("loc:remote_ambiguous")
            else:
                return {"score": 0, "tier": "drop", "matched_rules": matched + ["loc:non_uk"]}
        else:
            if is_manchester:
                s += 15
                matched.append("loc:manchester")
            elif is_london:
                s += 12
                matched.append("loc:london")
            elif is_uk:
                s += 6
                matched.append("loc:uk_other")
    elif remote_ambiguous:
        s -= 5
        matched.append("loc:remote_ambiguous")

    neg_t = set(m.group(0).lower() for m in NEG_TITLE.finditer(t_norm))
    if neg_t:
        s -= 14 * len(neg_t)
        matched.append(f"neg_title:{','.join(sorted(neg_t))}")

    if DATA_ENTRY.search(t_norm):
        s -= 20
        matched.append("data_entry")

    if IT_SUPPORT.search(f"{t_norm} {d_norm}"):
        s -= 12
        matched.append("it_support")

    if SENIOR.search(t_norm):
        s -= 12
        matched.append("senior")

    if FULLTIME.search(t_norm) and not intern_title:
        s -= 8
        matched.append("fulltime")

    neg_d = set(m.group(0).lower() for m in NEG_DESC.finditer(d_norm))
    if neg_d:
        s -= min(2 * len(neg_d), 6)
        matched.append(f"neg_desc:{len(neg_d)}")

    # Step 5. Apply overrides
    if is_test:
        s -= 100
        matched.append("is_test")

    # Step 6. Tier from score
    if s >= 25:
        tier = "high"
    elif s >= 15:
        tier = "medium"
    elif s >= 8:
        tier = "low"
    else:
        tier = "drop"

    if is_test:
        tier = "drop"

    s = min(s, 100)

    return {"score": s, "tier": tier, "matched_rules": matched}

