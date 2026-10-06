"""EditError: a refused edit (bad input, something that no longer exists). The web layer shows its message to the user."""


class EditError(Exception):
    """An edit that can't be made; the message is written for the user and shown as it is."""
