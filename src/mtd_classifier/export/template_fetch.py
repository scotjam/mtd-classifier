"""Fetch My Tax Digital's bridging templates on demand.

The SA103/SA105 XLSX templates are My Tax Digital's own copyrighted files
(HMRC publishes no bridging templates - every vendor designs their own), so
this app does not redistribute them. Instead each template is downloaded
once from mytaxdigital.co.uk, cached under the app data directory, and
checked against the SHA-256 this release was verified with. A changed hash
means the vendor updated the template - filling an unknown layout blindly
could put figures in the wrong HMRC boxes, so export refuses loudly and
asks for an app update rather than exporting quietly wrong files.
"""

from __future__ import annotations

import hashlib
import ssl
import urllib.error
import urllib.request
from pathlib import Path

import mtd_classifier.config as config

TEMPLATE_URLS = {
    "SA103": "https://mytaxdigital.co.uk/app/templates/MTD_SA103_template.xlsx",
    "SA105": "https://mytaxdigital.co.uk/app/templates/MTD_SA105_template.xlsx",
}

# SHA-256 of the template files this release's cell map (CATEGORY_BOX_CELLS
# in mytaxdigital.py) was decoded from and verified against. When My Tax
# Digital publishes changed templates, the mismatch surfaces as "update the
# app" - after re-verifying every cell coordinate, update these hashes.
TEMPLATE_SHA256 = {
    "SA103": "8e6faffb174a7d22f53729c36a785b2fa724314a4f7b4b5814c557c251198ca6",
    "SA105": "db5891e9f9134a0d197ff4993216939df9529bd58ca87ea4b517b3f610fcc23c",
}

# Belt and braces behind the hash: HMRC box numbers in their label cells.
LAYOUT_ANCHORS = {
    "SA103": {"A5": 15, "A29": 20, "A41": 23, "A65": 28, "A73": 30, "G5": 16, "A9": 81},
    "SA105": {"A5": "20a", "A19": 24, "A23": 25, "G23": "29a", "A40": 44, "G5": 21},
}


class TemplateError(Exception):
    """Base class for template fetch/validation problems."""


class TemplateDownloadError(TemplateError):
    """The template could not be downloaded (offline, site down, TLS)."""


class TemplateChangedError(TemplateError):
    """The template on mytaxdigital.co.uk no longer matches this release."""


def templates_cache_dir() -> Path:
    return config.APP_DIR / "templates"


_truststore_injected = False


def _urlopen(url: str):
    """urlopen that falls back to the OS trust store on certificate errors
    (corporate proxies / antivirus TLS interception use roots that only the
    OS store knows about)."""
    global _truststore_injected
    try:
        return urllib.request.urlopen(url, timeout=30)
    except urllib.error.URLError as e:
        cert_problem = isinstance(
            getattr(e, "reason", None), ssl.SSLCertVerificationError
        )
        if cert_problem and not _truststore_injected:
            try:
                import truststore
            except ImportError:
                raise e
            truststore.inject_into_ssl()
            _truststore_injected = True
            return urllib.request.urlopen(url, timeout=30)
        raise


def _download(form: str, dest: Path) -> None:
    url = TEMPLATE_URLS[form]
    try:
        with _urlopen(url) as resp:
            data = resp.read()
    except Exception as e:
        raise TemplateDownloadError(
            f"Could not download the {form} bridging template from\n{url}\n"
            f"({e}).\n\n"
            "Check your internet connection, or download the file manually "
            f"in a browser and save it as:\n{dest}"
        ) from e
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _check_hash(form: str, path: Path) -> bool:
    return hashlib.sha256(path.read_bytes()).hexdigest() == TEMPLATE_SHA256[form]


def validate_template(form: str, path: Path) -> None:
    """Pinned-hash check plus layout anchors. Raises TemplateChangedError."""
    if not _check_hash(form, path):
        raise TemplateChangedError(
            f"The {form} template published at mytaxdigital.co.uk has "
            "CHANGED since this version of the app was verified against it. "
            "Filling an unknown layout could put figures in the wrong HMRC "
            "boxes, so the export has been stopped.\n\n"
            "Please update the app - or, if no update exists yet, report "
            "this at https://github.com/scotjam/mtd-classifier/issues"
        )
    import openpyxl

    ws = openpyxl.load_workbook(path).worksheets[0]
    for cell, expected in LAYOUT_ANCHORS[form].items():
        actual = ws[cell].value
        if actual != expected and str(actual) != str(expected):
            raise TemplateChangedError(
                f"The {form} template failed layout validation (cell {cell} "
                f"is {actual!r}, expected {expected!r}) despite a matching "
                "hash - please report this at "
                "https://github.com/scotjam/mtd-classifier/issues"
            )


def get_template(form: str) -> Path:
    """The cached template for a form, downloading on first use and
    verifying it against this release's pinned SHA-256. Raises
    TemplateDownloadError / TemplateChangedError."""
    if form not in TEMPLATE_URLS:
        raise ValueError(f"Unknown form {form!r}")
    path = templates_cache_dir() / f"MTD_{form}_template.xlsx"
    if not path.exists() or not _check_hash(form, path):
        # missing, corrupted, or stale cache: (re)download once
        _download(form, path)
    validate_template(form, path)
    return path
