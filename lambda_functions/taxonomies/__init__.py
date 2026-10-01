# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Classification profiles: the domain-specific half of the classifier.

The agent, guardrails, schema validation, and batch pipeline are domain-neutral.
Everything that changes between domains (the code set, the input fields, the
output field names, and the wording of the prompt) lives in a
ClassificationProfile. Select one with the TAXONOMY environment variable.

The sample ships one profile, law_enforcement. To add a domain, create a
module in this package that defines PROFILE and register it in
_load_profiles() below.
"""

import math
import os
from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass(frozen=True)
class ContextField:
    """An optional input field that gives the agent classification context.

    aliases are accepted input names that map onto this field, so records
    produced by other tools (or by older examples) still classify correctly.
    """
    name: str
    aliases: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ClassificationProfile:
    name: str
    title: str
    # {code: human-readable description}. The model may only return these codes.
    taxonomy: Dict[str, str]
    # Code returned when the model output is invalid or the input is blocked.
    fallback_code: str
    # The untrusted free-text field being classified.
    primary_field: str
    context_fields: Tuple[ContextField, ...]
    # Output field names, kept per domain so results read naturally downstream.
    code_field: str
    description_field: str
    # Optional domain-specific string outputs: {field_name: prompt description}.
    extra_output_fields: Dict[str, str] = field(default_factory=dict)
    # Prompt wording.
    expert: str = ''
    item: str = ''
    taxonomy_label: str = ''
    research_steps: Tuple[str, ...] = ()
    guidance: Tuple[str, ...] = ()
    # XML-style tag that wraps untrusted input in the user prompt.
    data_tag: str = 'record_data'
    # Search tool exposed to the agent.
    search_tool_name: str = 'web_search'
    search_tool_description: str = ''

    def resolve_context(self, record: Dict) -> Dict[str, object]:
        """Map a raw input record onto this profile's canonical context fields."""
        resolved = {}
        for ctx in self.context_fields:
            for key in (ctx.name, *ctx.aliases):
                value = record.get(key)
                if not _is_blank(value):
                    resolved[ctx.name] = value
                    break
        return resolved


def _load_profiles() -> Dict[str, ClassificationProfile]:
    from . import law_enforcement
    return {p.name: p for p in (law_enforcement.PROFILE,)}


PROFILES = _load_profiles()
DEFAULT_PROFILE = 'law_enforcement'


def _is_blank(value) -> bool:
    """True for missing values, including the NaN pandas uses for empty CSV cells."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def get_profile(name: str = None) -> ClassificationProfile:
    """Return the named profile, or the one selected by the TAXONOMY env var."""
    name = name or os.environ.get('TAXONOMY', DEFAULT_PROFILE)
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"Unknown TAXONOMY {name!r}; expected one of {sorted(PROFILES)}")
