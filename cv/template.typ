// =============================================================================
// ATS-friendly CV template (Typst)
//
// Design rules that keep ATS parsers happy:
//   - single column, plain reading order (grid cells are emitted row by row)
//   - real text only: no icons, no small caps (the cause of "PROȷECTS"), no images
//   - ligatures and hyphenation OFF, so words extract exactly as written
//   - standard section headings
//   - full URLs written as visible text, not hidden behind icons
//
// Data comes from YAML. Pass a different file with:
//   typst compile template.typ out.pdf --input data=build/tailored.yaml
// =============================================================================

#let data = yaml(sys.inputs.at("data", default: "base.yaml"))
#let m = data.meta
#let accent = rgb("#1F3A5F")
#let muted = rgb("#4A4A4A")

#set document(title: m.name + " - CV", author: m.name)
#set page(paper: "a4", margin: (x: 1.4cm, top: 1.1cm, bottom: 1.0cm))
#set text(
  font: ("Lato", "New Computer Modern"),
  size: 10pt,
  lang: "en",
  region: "gb",
  hyphenate: false,
  ligatures: false,
  discretionary-ligatures: false,
  kerning: false, // kerned pairs like "Te" get extracted as "T e"
)
#set par(justify: false, leading: 0.5em, spacing: 0.55em)
#set list(marker: [•], indent: 0.3em, body-indent: 0.5em, spacing: 0.5em)
// Never break a line inside "CI/CD", "full-stack", "GPT-2" etc. Split keywords miss ATS matches.
#show regex("[\w.+#]+(?:[/-][\w.+#]+)+"): it => box(it)

// ---------- helpers ----------
#let section(title) = block(
  above: 1.0em,
  below: 0.55em,
  width: 100%,
  inset: (bottom: 2.5pt),
  stroke: (bottom: 0.6pt + accent),
  text(size: 10.8pt, weight: "bold", fill: accent, upper(title)),
)

#let head-row(lhs, rhs, l-style: "normal", l-weight: "bold", r-style: "normal") = grid(
  columns: (1fr, auto),
  column-gutter: 1em,
  text(weight: l-weight, style: l-style, lhs),
  align(right, text(style: r-style, fill: muted, rhs)),
)

#let bullets(items) = list(..items.map(b => b.text))

// ---------- header ----------
#align(center, {
  text(size: 20pt, weight: "bold", fill: accent, m.name)
  v(-0.35em)
  let parts = (
    m.at("location", default: none),
    m.phone,
    link("mailto:" + m.email, m.email),
    link("https://" + m.linkedin, m.linkedin),
    link("https://" + m.github, m.github),
  ).filter(p => p != none)
  text(size: 9.3pt, fill: muted, parts.join("  |  "))
})

// ---------- education ----------
#section("Education")
#for e in data.education {
  head-row(e.institution, e.location)
  v(-0.25em)
  let deg = e.degree + if e.at("note", default: none) != none { " - " + e.note } else { "" }
  head-row(deg, e.dates, l-weight: "regular", l-style: "italic", r-style: "italic")
  if e.at("coursework", default: ()).len() > 0 {
    v(-0.15em)
    list([*Relevant Coursework:* #e.coursework.join(", ")])
  }
}

// ---------- skills ----------
#section("Technical Skills")
#for s in data.skills {
  block(below: 0.5em, par(hanging-indent: 1em)[*#s.category:* #s.items.join(", ")])
}

// ---------- experience ----------
#section("Experience")
#for (i, x) in data.experience.enumerate() {
  if i > 0 { v(0.3em) }
  head-row(x.org, x.location)
  v(-0.25em)
  head-row(x.role, x.dates, l-weight: "regular", l-style: "italic", r-style: "italic")
  v(-0.1em)
  bullets(x.bullets)
}

// ---------- projects ----------
#section("Projects")
#for (i, p) in data.projects.enumerate() {
  if i > 0 { v(0.3em) }
  grid(
    columns: (1fr, auto),
    column-gutter: 1em,
    [*#p.name* #text(fill: muted)[ | #emph(p.tech.join(", "))]],
    align(right, text(fill: muted, p.dates)),
  )
  v(-0.1em)
  bullets(p.bullets)
}

// ---------- certifications ----------
#if data.at("certifications", default: ()).len() > 0 {
  section("Certifications")
  list(..data.certifications.map(c => [#c.issuer: #c.name (#c.year)]))
}
