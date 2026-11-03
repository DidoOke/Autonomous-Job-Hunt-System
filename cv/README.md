# CV Builder & ATS Verification Suite

This module provides an ATS-optimized CV rendering system built on [Typst](https://typst.app/).

## Why Typst for Resumes?
- **Deterministic layout**: Emits identical PDF structure across Linux, macOS, and Windows.
- **ATS-Optimized**: Ligatures and kerning traps are disabled, URLs remain plain readable text, and section headings follow standard ATS patterns.
- **Automated Validation**: `build.py` compiles the CV and immediately acts as an ATS parser, re-extracting text to verify that every single bullet point, contact detail, and keyword can be parsed without truncation or character corruption.

## Quickstart

1. **Install requirements**:
   ```bash
   pip install typst pypdf pyyaml
   ```

2. **Customize your information**:
   Edit `cv/base.yaml` with your contact details, education, skills, work experience, and projects.

3. **Compile to PDF**:
   ```bash
   python cv/build.py
   ```
   Outputs to `cv/out/cv.pdf`.

4. **Compile with PNG preview and ATS check**:
   ```bash
   python cv/build.py --png
   ```

5. **Test Job Description (JD) keyword coverage**:
   ```bash
   python cv/build.py --keywords "Python,Docker,PostgreSQL,Distributed Systems"
   ```

