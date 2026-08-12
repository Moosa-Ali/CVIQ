import re

from .models import CVData


def validate(cv: CVData) -> list[str]:
    warnings: list[str] = []
    personal = cv.personal

    if not personal.name.strip():
        warnings.append("Name is missing.")

    if personal.name.strip() and not (personal.email.strip() or personal.phone.strip()):
        warnings.append("No email or phone contact provided.")

    if personal.email and not re.match(r"^[\w.+-]+@[\w-]+\.[\w.-]+$", personal.email.strip()):
        warnings.append(f"Email address looks invalid: {personal.email}")

    if personal.phone and not re.search(r"\d{7,}", personal.phone):
        warnings.append(f"Phone number looks too short: {personal.phone}")

    if not cv.summary.strip() and cv.experience:
        warnings.append("No professional summary; consider adding one.")

    if not cv.experience:
        warnings.append("No work experience listed.")
    else:
        for i, item in enumerate(cv.experience, 1):
            if not item.role.strip() and not item.company.strip():
                warnings.append(f"Experience entry #{i} is missing a role or company.")
            if item.role and not item.bullets:
                warnings.append(f"Experience role '{item.role}' has no bullet points.")

    if not cv.education:
        warnings.append("No education listed.")

    if not cv.skills:
        warnings.append("No skills listed.")
    else:
        for group in cv.skills:
            if not group.skills:
                warnings.append(f"Skill group '{group.category or '(uncategorized)'}' is empty.")

    if not cv.languages:
        warnings.append("No languages listed.")

    return warnings
