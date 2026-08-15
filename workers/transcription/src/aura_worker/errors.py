from __future__ import annotations

from score_schema.models import JobErrorCode


class JobFailure(Exception):
    def __init__(self, code: JobErrorCode, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")
