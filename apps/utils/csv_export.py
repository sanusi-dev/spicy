"""CSV register exports — stdlib csv only, Excel-friendly UTF-8 with BOM."""

import csv
import re

from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone

EXPORT_ROW_CAP = 50000


class _Echo:
    """csv.writer sink that echoes rows back as strings."""

    def write(self, value):
        return value


def money_text(value):
    """Format a decimal for Excel: raw two-decimal value, no symbol or separators."""
    return f"{value:.2f}" if value is not None else ""


def text(value):
    return "" if value is None else str(value)


def export_filename(register, params):
    """Build register + date + active-filter filename, e.g. orders-2026-09-11-status-SUBMITTED.csv."""
    bits = [register, timezone.localdate().isoformat()]
    for key, value in params.items():
        clean = re.sub(r"[^A-Za-z0-9_-]+", "", str(value or ""))
        if clean:
            bits.extend([key, clean])
    return "-".join(bits) + ".csv"


def stream_csv(filename, header, row_iter):
    """Stream a BOM-prefixed CSV download."""
    writer = csv.writer(_Echo())

    def stream():
        yield "\ufeff"
        yield writer.writerow(header)
        for row in row_iter:
            yield writer.writerow(row)

    response = StreamingHttpResponse(stream(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def over_row_cap(queryset):
    """Return a 400 response when the export exceeds the row cap, else None."""
    if queryset.count() > EXPORT_ROW_CAP:
        return HttpResponse(
            f"This export exceeds {EXPORT_ROW_CAP:,} rows. Narrow the filters and try again.",
            status=400,
        )
    return None
