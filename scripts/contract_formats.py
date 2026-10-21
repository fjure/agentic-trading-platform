"""Verbindliche UTC-Formatprüfung auch ohne optionale JSON-Schema-Extras."""

import re
from datetime import datetime

from jsonschema import FormatChecker

_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\Z")


def contract_formats():
    checker = FormatChecker()

    @checker.checks("date-time", raises=(ValueError, OverflowError))
    def utc_timestamp(value):
        if not isinstance(value, str):
            return True
        if not _UTC.fullmatch(value):
            return False
        return datetime.fromisoformat(value).utcoffset().total_seconds() == 0

    return checker
