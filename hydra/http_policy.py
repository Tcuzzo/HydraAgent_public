"""Apply caller URL policy before the initial request and every redirect."""
from __future__ import annotations

from typing import Callable
import urllib.request


class PolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, validate: Callable[[str], None]):
        super().__init__()
        self.validate = validate

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.validate(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_checked(request: urllib.request.Request, *, validate: Callable[[str], None], timeout: float):
    validate(request.full_url)
    opener = urllib.request.build_opener(PolicyRedirectHandler(validate))
    return opener.open(request, timeout=timeout)
