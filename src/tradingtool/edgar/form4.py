"""Parser de Form 4 / 4/A de EDGAR (XML ``ownershipDocument`` y archivo de envío completo).

Fuentes de formato verificadas con filings reales (esquemas X0407, X0508, X0609):
- El archivo de envío completo (``<accession>.txt``) trae una cabecera SGML con
  ``<ACCEPTANCE-DATETIME>YYYYMMDDHHMMSS`` (hora del Este de EE. UU.), ``ACCESSION NUMBER``,
  ``CONFORMED SUBMISSION TYPE``, ``FILED AS OF DATE`` (YYYYMMDD) y
  ``CONFORMED PERIOD OF REPORT``; el XML va entre ``<XML>`` y ``</XML>`` dentro del
  ``<DOCUMENT>`` cuyo ``<TYPE>`` es 4 o 4/A.
- ``aff10b5One`` (casilla de plan 10b5-1) es un elemento a nivel de documento; puede venir
  como ``0/1`` o ``false/true``.
- Los valores numéricos/fechas suelen venir envueltos en ``<value>``; a veces solo hay un
  ``<footnoteId>`` sin valor (entonces el valor es desconocido -> None).

El XML es dato no confiable: se parsea con ``defusedxml`` (sin entidades externas).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from xml.etree.ElementTree import Element  # solo para tipos

from defusedxml import ElementTree as SafeET

from tradingtool.models import Form4Filing, InsiderTransaction, ReportingOwner
from tradingtool.tickers import normalize_ticker

__all__ = ["normalize_ticker"]

FORM4_TYPES = ("4", "4/A")

_HEADER_PATTERNS = {
    "acceptance": re.compile(r"<ACCEPTANCE-DATETIME>\s*(\d{14})"),
    "accession": re.compile(r"ACCESSION NUMBER:\s*([\d-]+)"),
    "form_type": re.compile(r"CONFORMED SUBMISSION TYPE:\s*(\S+)"),
    "filed": re.compile(r"FILED AS OF DATE:\s*(\d{8})"),
    "period": re.compile(r"CONFORMED PERIOD OF REPORT:\s*(\d{8})"),
}
_DOCUMENT_RE = re.compile(r"<DOCUMENT>(.*?)</DOCUMENT>", re.DOTALL | re.IGNORECASE)
_TYPE_RE = re.compile(r"<TYPE>\s*([^\s<]+)", re.IGNORECASE)
_XML_RE = re.compile(r"<XML>(.*?)</XML>", re.DOTALL | re.IGNORECASE)
# "10b5-1", "10b5 1", "10b-5-1", "10b5–1" (guiones tipográficos incluidos)
PLAN_10B5_1_RE = re.compile(r"10b[\s\-\u2010-\u2014]?5[\s\-\u2010-\u2014]?1", re.IGNORECASE)


class Form4ParseError(ValueError):
    pass


# ----------------------------------------------------------------------------- utilidades


def _text(el: Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def _find_text(parent: Element | None, path: str) -> str | None:
    """Texto de ``path``; si el nodo tiene hijo ``<value>``, usa ese."""
    if parent is None:
        return None
    el = parent.find(path)
    if el is None:
        return None
    val = el.find("value")
    return _text(val) if val is not None else _text(el)


def _parse_bool(raw: str | None) -> bool | None:
    if raw is None:
        return None
    r = raw.strip().lower()
    if r in ("1", "true", "y", "yes"):
        return True
    if r in ("0", "false", "n", "no"):
        return False
    return None


def _parse_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw.replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    raw = raw.strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def normalize_cik(raw: str | None) -> str | None:
    if raw is None:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits.zfill(10) if digits else None


def normalize_accession(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 18:
        raise Form4ParseError(f"número de accesión inválido: {raw!r}")
    return f"{digits[:10]}-{digits[10:12]}-{digits[12:]}"


def _footnote_ids(el: Element) -> tuple[str, ...]:
    ids = []
    for fn in el.iter("footnoteId"):
        fid = fn.get("id")
        if fid and fid not in ids:
            ids.append(fid)
    return tuple(ids)


# ----------------------------------------------------------------------------- XML


def _parse_owner(el: Element) -> ReportingOwner | None:
    cik = normalize_cik(_find_text(el, "reportingOwnerId/rptOwnerCik"))
    if cik is None:
        return None
    rel = el.find("reportingOwnerRelationship")
    return ReportingOwner(
        owner_cik=cik,
        owner_name=_find_text(el, "reportingOwnerId/rptOwnerName") or "",
        is_director=bool(_parse_bool(_find_text(rel, "isDirector"))),
        is_officer=bool(_parse_bool(_find_text(rel, "isOfficer"))),
        is_ten_pct_owner=bool(_parse_bool(_find_text(rel, "isTenPercentOwner"))),
        is_other=bool(_parse_bool(_find_text(rel, "isOther"))),
        officer_title=_find_text(rel, "officerTitle"),
    )


def _parse_transaction(el: Element, seq: int, is_derivative: bool) -> InsiderTransaction:
    return InsiderTransaction(
        seq=seq,
        is_derivative=is_derivative,
        security_title=_find_text(el, "securityTitle"),
        transaction_date=_parse_date(_find_text(el, "transactionDate")),
        transaction_code=(_find_text(el, "transactionCoding/transactionCode") or None),
        shares=_parse_float(_find_text(el, "transactionAmounts/transactionShares")),
        price_per_share=_parse_float(_find_text(el, "transactionAmounts/transactionPricePerShare")),
        acquired_disposed=_find_text(el, "transactionAmounts/transactionAcquiredDisposedCode"),
        shares_owned_after=_parse_float(
            _find_text(el, "postTransactionAmounts/sharesOwnedFollowingTransaction")
        ),
        direct_indirect=_find_text(el, "ownershipNature/directOrIndirectOwnership"),
        equity_swap=_parse_bool(_find_text(el, "transactionCoding/equitySwapInvolved")),
        footnote_ids=_footnote_ids(el),
    )


def parse_ownership_xml(
    xml: str | bytes,
    *,
    accession: str,
    filing_date: date,
    acceptance_ts: datetime | None = None,
    form_type: str | None = None,
    source: str = "edgar_daily",
) -> Form4Filing:
    """Convierte un ``ownershipDocument`` en :class:`Form4Filing`."""
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", errors="replace")
    try:
        root = SafeET.fromstring(xml.strip())
    except Exception as exc:  # ParseError, DefusedXmlException
        raise Form4ParseError(f"XML inválido en {accession}: {exc}") from exc
    if root.tag != "ownershipDocument":
        raise Form4ParseError(f"{accession}: raíz inesperada {root.tag!r}")

    doc_type = _find_text(root, "documentType") or form_type or ""
    issuer = root.find("issuer")
    issuer_cik = normalize_cik(_find_text(issuer, "issuerCik"))
    if issuer_cik is None:
        raise Form4ParseError(f"{accession}: falta issuerCik")

    owners = tuple(o for o in (_parse_owner(e) for e in root.findall("reportingOwner")) if o)

    transactions: list[InsiderTransaction] = []
    for e in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        transactions.append(_parse_transaction(e, len(transactions), is_derivative=False))
    for e in root.findall("derivativeTable/derivativeTransaction"):
        transactions.append(_parse_transaction(e, len(transactions), is_derivative=True))

    footnotes = {}
    for fn in root.findall("footnotes/footnote"):
        fid = fn.get("id")
        if fid:
            footnotes[fid] = " ".join("".join(fn.itertext()).split())
    remarks = (
        " ".join("".join(root.find("remarks").itertext()).split())
        if root.find("remarks") is not None
        else ""
    )
    mentions = any(PLAN_10B5_1_RE.search(t) for t in [*footnotes.values(), remarks])

    return Form4Filing(
        accession=normalize_accession(accession),
        source=source,
        form_type=(form_type or doc_type).upper(),
        filing_date=filing_date,
        acceptance_ts=acceptance_ts,
        period_of_report=_parse_date(_find_text(root, "periodOfReport")),
        issuer_cik=issuer_cik,
        issuer_name=_find_text(issuer, "issuerName"),
        issuer_ticker=normalize_ticker(_find_text(issuer, "issuerTradingSymbol")),
        aff10b5one=_parse_bool(_find_text(root, "aff10b5One")),
        owners=owners,
        transactions=tuple(transactions),
        footnotes=footnotes,
        mentions_10b5_1=mentions,
    )


# ----------------------------------------------------------------------------- envío completo


def parse_sec_header(text: str) -> dict[str, object]:
    """Extrae campos clave de la cabecera SGML de un envío completo."""
    head = text[: text.find("</SEC-HEADER>")] if "</SEC-HEADER>" in text else text[:20000]
    out: dict[str, object] = {}
    m = _HEADER_PATTERNS["acceptance"].search(head)
    out["acceptance_ts"] = datetime.strptime(m.group(1), "%Y%m%d%H%M%S") if m else None
    m = _HEADER_PATTERNS["accession"].search(head)
    out["accession"] = normalize_accession(m.group(1)) if m else None
    m = _HEADER_PATTERNS["form_type"].search(head)
    out["form_type"] = m.group(1).upper() if m else None
    m = _HEADER_PATTERNS["filed"].search(head)
    out["filing_date"] = _parse_date(m.group(1)) if m else None
    m = _HEADER_PATTERNS["period"].search(head)
    out["period_of_report"] = _parse_date(m.group(1)) if m else None
    return out


def parse_submission_text(text: str, source: str = "edgar_daily") -> Form4Filing:
    """Parsea un archivo de envío completo de EDGAR que contiene un Form 4 o 4/A."""
    header = parse_sec_header(text)
    accession = header.get("accession")
    filing_date = header.get("filing_date")
    form_type = header.get("form_type")
    if not accession or not filing_date:
        raise Form4ParseError("cabecera SEC incompleta (sin accesión o fecha de presentación)")
    if form_type not in FORM4_TYPES:
        raise Form4ParseError(f"{accession}: no es Form 4 (tipo {form_type!r})")
    for doc in _DOCUMENT_RE.findall(text):
        tm = _TYPE_RE.search(doc)
        if not tm or tm.group(1).upper() not in FORM4_TYPES:
            continue
        xm = _XML_RE.search(doc)
        if not xm:
            continue
        return parse_ownership_xml(
            xm.group(1),
            accession=str(accession),
            filing_date=filing_date,  # type: ignore[arg-type]
            acceptance_ts=header.get("acceptance_ts"),  # type: ignore[arg-type]
            form_type=str(form_type),
            source=source,
        )
    raise Form4ParseError(f"{accession}: no se encontró el XML del Form 4")
