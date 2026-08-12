from .models import CVData, SkillGroup


def _contact_line(cv: CVData) -> str:
    personal = cv.personal
    parts = [
        personal.email,
        personal.phone,
        personal.location,
        personal.website,
        personal.linkedin,
        personal.github,
    ]
    return " | ".join(part for part in parts if part)


def cv_to_text(cv: CVData) -> str:
    lines: list[str] = []
    personal = cv.personal
    lines.append(personal.name)
    if personal.title:
        lines.append(personal.title)
    contact = _contact_line(cv)
    if contact:
        lines.append(contact)
    lines.append("")

    if cv.summary:
        lines.append("SUMMARY")
        lines.append(cv.summary)
        lines.append("")

    if cv.experience:
        lines.append("EXPERIENCE")
        for item in cv.experience:
            header = item.role if item.role else item.company
            if item.role and item.company:
                header = f"{item.role} at {item.company}"
            lines.append(header)
            meta = []
            if item.company and not item.role:
                meta.append(item.company)
            if item.location:
                meta.append(item.location)
            if item.dates.start or item.dates.end:
                meta.append(f"{item.dates.start} - {item.dates.end}")
            if meta:
                lines.append(" | ".join(meta))
            for bullet in item.bullets:
                lines.append(f"- {bullet}")
        lines.append("")

    if cv.education:
        lines.append("EDUCATION")
        for item in cv.education:
            name = " ".join(x for x in [item.degree, item.field] if x) or item.institution
            lines.append(name)
            meta = []
            if item.institution and name != item.institution:
                meta.append(item.institution)
            if item.gpa:
                meta.append(f"GPA: {item.gpa}")
            if item.dates.start or item.dates.end:
                meta.append(f"{item.dates.start} - {item.dates.end}")
            if meta:
                lines.append(" | ".join(meta))
        lines.append("")

    if cv.skills:
        lines.append("SKILLS")
        for group in cv.skills:
            label = f"{group.category}: " if group.category else ""
            lines.append(label + ", ".join(group.skills) if group.skills else label.strip())
        lines.append("")

    if cv.projects:
        lines.append("PROJECTS")
        for project in cv.projects:
            lines.append(project.name + (f" ({project.link})" if project.link else ""))
            if project.description:
                lines.append(project.description)
            for bullet in project.bullets:
                lines.append(f"- {bullet}")
        lines.append("")

    if cv.certifications:
        lines.append("CERTIFICATIONS")
        for cert in cv.certifications:
            line = cert.name
            suffix = " | ".join(x for x in [cert.issuer, cert.year] if x)
            if suffix:
                line = f"{line} ({suffix})"
            lines.append(line)
        lines.append("")

    if cv.languages:
        lines.append("LANGUAGES")
        for language in cv.languages:
            line = language.name
            if language.level:
                line = f"{line} ({language.level})"
            lines.append(line)
        lines.append("")

    return "\n".join(lines).strip()
