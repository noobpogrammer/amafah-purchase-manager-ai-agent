"""
Code-level safety guardrails for validating outbound messages and preventing prompt injection leaks.
"""

import re

# Compiled regex patterns for unsafe content (code blocks, Python code, injection phrases)
UNSAFE_PATTERNS = [
    r"```",                                           # Code blocks / Markdown backticks
    r"\bdef\s+\w+\s*\(",                              # Python function definition
    r"\bimport\s+[\w\.]+",                            # Python import statement
    r"\bfrom\s+[\w\.]+\s+import\b",                   # Python from ... import statement
    r"\bclass\s+\w+\b",                               # Python class definition
    r"\bignore\s+(?:all\s+)?previous\s+instructions\b", # Injection phrase
    r"\byou\s+are\s+now\b",                           # Persona shift
    r"\bact\s+as\s+a\b",                              # Persona shift
    r"\bsystem\s+prompt\b",                           # System prompt leak
    r"\breveal\s+(?:your\s+)?(?:system\s+)?instructions\b", # Prompt leak request
]

def is_safe_to_send(text: str) -> bool:
    """
    Validates outbound text before sending to suppliers.
    Returns False if text contains code blocks, Python code statements, or prompt injection phrases.
    """
    if not text or not isinstance(text, str):
        return True

    text_lower = text.lower()
    for pattern in UNSAFE_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return False
    return True
