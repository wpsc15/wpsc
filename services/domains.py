import re


def validate_domain(domain: str) -> str:
    """Return a normalized ASCII hostname or reject unsafe/invalid input."""
    value = domain.strip().rstrip(".").lower()
    labels = value.split(".")
    if (
        len(value) > 253
        or not labels
        or any(
            not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                label,
            )
            for label in labels
        )
    ):
        raise ValueError("Enter a valid ASCII domain name.")
    return value
