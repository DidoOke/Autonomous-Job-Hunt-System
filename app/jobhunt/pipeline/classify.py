import re
from typing import Optional
from jobhunt.core.schemas import Job

UK_KEYWORDS = [
    "united kingdom",
    " uk",
    ", uk",
    "(uk)",
    "great britain",
    "england",
    "scotland",
    "wales",
    "northern ireland",
    "london",
    "manchester",
    "salford",
    "leeds",
    "edinburgh",
    "glasgow",
    "bristol",
    "cambridge",
    "oxford",
    "birmingham",
    "belfast",
    "reading",
    "newcastle",
    "sheffield",
    "liverpool",
    "nottingham",
    "milton keynes",
    "guildford",
    "brighton",
    "bath",
    "exeter",
    "warrington",
    "knutsford",
    "cheltenham",
    "macclesfield",
    "crewe",
    "wilmslow",
    "coventry",
    "southampton",
    "swindon",
]

KIND_PATTERNS = [
    ("summer_internship", re.compile(r"\b(summer\s+(intern(ship)?|analyst|associate|developer|engineer))\b", re.I)),
    ("spring_week", re.compile(r"\b(spring\s+week|spring\s+insight|insight\s+(week|programme|program)|first\s+year\s+insight)\b", re.I)),
    ("winter_spring", re.compile(r"\b((winter|spring)\s+(intern(ship)?|analyst))\b", re.I)),
    ("placement", re.compile(r"\b(placement|industrial\s+placement|year\s+in\s+industry|12\s+month|co-?op)\b", re.I)),
    ("apprenticeship", re.compile(r"\b(apprentice(ship)?|degree\s+apprentice)\b", re.I)),
    ("grad", re.compile(r"\b(graduate|grad\s+program|grad\s+scheme|new\s+grad|entry\s+level)\b", re.I)),
    ("summer_internship", re.compile(r"\b(intern(ship)?|interns)\b", re.I)),
]

ML_AI_RE = re.compile(
    r"\b(machine\s+learning|\bml\b|artificial\s+intelligence|\bai\b|deep\s+learning|"
    r"nlp|computer\s+vision|llm|neural|research\s+scientist|research\s+engineer)\b",
    re.I,
)
DEVOPS_RE = re.compile(
    r"\b(devops|sre|site\s+reliability|infrastructure|cloud\s+engineer|platform\s+engineer|"
    r"sysadmin|systems\s+engineer|kubernetes|aws|azure)\b",
    re.I,
)
DATA_RE = re.compile(
    r"\b(data\s+analyst|data\s+engineer|data\s+science|data\s+scientist|analytics|bi\s+developer|"
    r"business\s+intelligence|database|sql\s+developer)\b",
    re.I,
)
SWE_RE = re.compile(
    r"\b(software|developer|frontend|front-end|backend|back-end|fullstack|full-stack|full\s+stack|"
    r"engineer|mobile|flutter|ios|android|web\s+developer|python|java|c\+\+|golang|rust|react|"
    r"node|embedded|firmware|qa|test\s+engineer|sdit|sdet)\b",
    re.I,
)

TEST_PATTERNS = re.compile(
    r"("
    r"\b(?!(?:latest|contest|protest|greatest|fastest|smartest)\b)\w*test[a-z0-9_-]*(?![\s_-]+(?:\w+[\s_-]+){0,2}(?:engineer|analyst|developer|manager|lead|architect|specialist|consultant|coordinator|automation|qa|ops|scientist|designer|writer|strategist|planner|director|senior|principal|graduate|junior|intern|associate|technician|design))|"
    r"\b(?:canary|placeholder|dummy)\b|"
    r"\w*sample[\s_-]*job\b|"
    r"\b(?:job|vacancy|posting)\s+test\b"
    r")",
    re.I,
)

UK_COUNTRY_RE = re.compile(
    r"\b(gb|uk|united kingdom|great britain|england|scotland|wales|northern ireland)\b",
    re.I,
)


def classify(job: Job) -> Job:
    loc = (job.location_raw or "").lower().strip()
    title = (job.title or "").lower().strip()
    desc = (job.description or "").lower().strip()
    content = f"{title} {desc[:500]}"

    is_remote = "remote" in loc
    remote_ambiguous = False

    # REMOTE HANDLING: if is_remote=true but location_raw is exactly "Remote"
    # (no country), set is_uk=false and add a flag remote_ambiguous=true.
    # Do not assume global remote = UK. This is the GitLab/Grafana fix.
    if is_remote and loc in ["remote", "remote - global", "anywhere", "remote /", "global remote"]:
        is_uk = False
        remote_ambiguous = True
    elif is_remote:
        has_uk = any(uk_loc in loc for uk_loc in UK_KEYWORDS) or bool(UK_COUNTRY_RE.search(loc))
        if has_uk:
            is_uk = True
            remote_ambiguous = False
        else:
            is_uk = False
            remote_ambiguous = True
    else:
        if re.search(r",\s*(us|usa|ca|au|de|fr|in|es|it|nl|se|no|dk|pl|ua|br|bo|eg|dz|pk|vn|ph|my|al|za)\b", loc):
            is_uk = bool(UK_COUNTRY_RE.search(loc))
        else:
            is_uk = any(uk_loc in loc for uk_loc in UK_KEYWORDS) or bool(UK_COUNTRY_RE.search(loc))
        remote_ambiguous = False

    is_manchester = "manchester" in loc or "salford" in loc
    is_london = "london" in loc

    # is_test
    is_test = bool(TEST_PATTERNS.search(title))

    # job_kind
    job_kind = "other"
    for kind, pattern in KIND_PATTERNS:
        if pattern.search(title):
            job_kind = kind
            break

    # role_family
    if ML_AI_RE.search(title) or ML_AI_RE.search(content):
        role_family = "ml_ai"
    elif DEVOPS_RE.search(title) or DEVOPS_RE.search(content):
        role_family = "devops"
    elif DATA_RE.search(title) or DATA_RE.search(content):
        role_family = "data"
    elif SWE_RE.search(title) or SWE_RE.search(content):
        role_family = "swe"
    else:
        role_family = "other"

    job.is_uk = is_uk
    job.is_manchester = is_manchester
    job.is_london = is_london
    job.is_remote = is_remote
    job.remote_ambiguous = remote_ambiguous
    job.is_test = is_test
    job.job_kind = job_kind
    job.role_family = role_family

    return job
