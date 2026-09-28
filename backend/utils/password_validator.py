import re


def validate_password(password: str) -> tuple[bool, str | None]:
    if len(password) < 8:
        return False, "Passwort muss mindestens 8 Zeichen haben"
    if not re.search(r"[A-Z]", password):
        return False, "Passwort muss mindestens einen Großbuchstaben haben"
    if not re.search(r"[a-z]", password):
        return False, "Passwort muss mindestens einen Kleinbuchstaben haben"
    if not re.search(r"[0-9]", password):
        return False, "Passwort muss mindestens eine Zahl haben"
    if not re.search(r"[^A-Za-z0-9]", password):
        return False, "Passwort muss mindestens ein Sonderzeichen haben"
    return True, None

