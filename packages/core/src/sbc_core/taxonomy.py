"""Service taxonomy: canonical IDs for SBC events and services, and user wording mapped to them.

The data lives in ``data/services.yaml``. At build time :meth:`Taxonomy.classify_row` gives every benefit
row a stable ``service_id``. At runtime :meth:`Taxonomy.match_term` turns what a user typed into service IDs
or drug tags. Matching here is exact after normalization; embedding fallback and LLM disambiguation sit on top
of it in the retrieval package.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DRUG_EVENT = "drugs"
_ID = r"^[a-z][a-z0-9_]*$"


class TaxonomyError(Exception):
    """The taxonomy file is inconsistent, or a label is not in it."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventDef(_Model):
    id: str = Field(pattern=_ID)
    label: str


class ServiceDef(_Model):
    id: str = Field(pattern=_ID)
    event: str
    labels: tuple[str, ...] = Field(min_length=1)
    synonyms: tuple[str, ...] = ()


class DrugLabel(_Model):
    label: str
    tier: str = Field(pattern=r"^[1-9][a-z]?$")
    tags: tuple[str, ...] = Field(min_length=1)

    @property
    def service_id(self) -> str:
        return f"rx_tier_{self.tier}"


class _TaxonomyFile(_Model):
    version: int
    events: tuple[EventDef, ...]
    services: tuple[ServiceDef, ...]
    drug_labels: tuple[DrugLabel, ...]
    drug_terms: dict[str, tuple[str, ...]]
    ambiguous_terms: dict[str, tuple[str, ...]]

    @model_validator(mode="after")
    def _check(self) -> _TaxonomyFile:
        event_ids = [e.id for e in self.events]
        _unique(event_ids, "event id")
        _unique([e.label for e in self.events], "event label")
        if DRUG_EVENT not in event_ids:
            raise ValueError(f"the '{DRUG_EVENT}' event is required")
        service_ids = [s.id for s in self.services]
        _unique(service_ids, "service id")
        for service in self.services:
            if service.event not in event_ids:
                raise ValueError(f"service {service.id}: unknown event {service.event}")
            if service.event == DRUG_EVENT:
                raise ValueError(f"service {service.id}: drug rows belong in drug_labels")
        _unique([(s.event, label) for s in self.services for label in s.labels], "(event, label) pair")
        _unique([d.label for d in self.drug_labels], "drug label")
        tags = {t for d in self.drug_labels for t in d.tags}
        unknown_tags = set(self.drug_terms) - tags - {"any_drug"}
        if unknown_tags:
            raise ValueError(f"drug_terms use tags no drug label has: {sorted(unknown_tags)}")
        known_ids = set(service_ids)
        for term, ids in self.ambiguous_terms.items():
            if len(ids) < 2 or set(ids) - known_ids:
                raise ValueError(f"ambiguous term {term!r} must list two or more known service ids")
        terms = [normalize(t) for s in self.services for t in s.synonyms]
        terms += [normalize(t) for ts in self.drug_terms.values() for t in ts]
        terms += [normalize(t) for t in self.ambiguous_terms]
        _unique(terms, "user term (synonyms, drug terms and ambiguous terms must not overlap)")
        return self


def _unique(values: list[Any], what: str) -> None:
    seen: set[Any] = set()
    dupes: set[Any] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    if dupes:
        raise ValueError(f"duplicate {what}: {sorted(map(str, dupes))}")


_SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, straighten quotes, collapse whitespace and trim surrounding punctuation."""
    text = unicodedata.normalize("NFKC", text).replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    return _SPACE_RE.sub(" ", text.lower()).strip(" .,;:!?\"'")


@dataclass(frozen=True)
class RowClass:
    """Canonical identity of one SBC benefit row."""

    event_id: str
    service_id: str
    kind: Literal["medical", "drug"]
    drug_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class TermMatch:
    """What a user's wording refers to. Empty when nothing matched."""

    service_ids: tuple[str, ...] = ()
    drug_tags: tuple[str, ...] = ()
    ambiguous: bool = False

    def __bool__(self) -> bool:
        return bool(self.service_ids or self.drug_tags)


class Taxonomy:
    def __init__(self, data: _TaxonomyFile) -> None:
        self.version = data.version
        self.events = {e.id: e for e in data.events}
        self.services = {s.id: s for s in data.services}
        self.drug_labels = {d.label: d for d in data.drug_labels}
        self._event_by_label = {e.label: e.id for e in data.events}
        self._service_by_row = {(s.event, label): s.id for s in data.services for label in s.labels}
        self._terms: dict[str, TermMatch] = {}
        for service in data.services:
            for term in service.synonyms:
                self._terms[normalize(term)] = TermMatch(service_ids=(service.id,))
        for tag, terms in data.drug_terms.items():
            for term in terms:
                self._terms[normalize(term)] = TermMatch(drug_tags=(tag,))
        for term, ids in data.ambiguous_terms.items():
            self._terms[normalize(term)] = TermMatch(service_ids=ids, ambiguous=True)

    @property
    def drug_service_ids(self) -> tuple[str, ...]:
        return tuple(sorted({d.service_id for d in self.drug_labels.values()}))

    def event_id(self, event_label: str) -> str:
        try:
            return self._event_by_label[event_label]
        except KeyError:
            raise TaxonomyError(f"unknown SBC event label: {event_label!r}") from None

    def classify_row(self, event_label: str, service_label: str) -> RowClass:
        """Canonical identity of a benefit row from its verbatim event and service labels."""
        event_id = self.event_id(event_label)
        if event_id == DRUG_EVENT:
            drug = self.drug_labels.get(service_label)
            if drug is None:
                raise TaxonomyError(f"unknown drug tier label: {service_label!r}")
            return RowClass(event_id, drug.service_id, "drug", drug.tags)
        service_id = self._service_by_row.get((event_id, service_label))
        if service_id is None:
            raise TaxonomyError(f"unknown service label {service_label!r} under {event_label!r}")
        return RowClass(event_id, service_id, "medical")

    def match_term(self, text: str) -> TermMatch:
        """Exact match of user wording against synonyms, drug terms and ambiguous terms."""
        return self._terms.get(normalize(text), TermMatch())


@cache
def load_taxonomy() -> Taxonomy:
    raw = files("sbc_core").joinpath("data/services.yaml").read_text(encoding="utf-8")
    try:
        return Taxonomy(_TaxonomyFile.model_validate(yaml.safe_load(raw)))
    except ValueError as exc:
        raise TaxonomyError(str(exc)) from None
