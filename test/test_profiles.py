# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Unit tests for classification profiles, verified citations, and the review flag.

Covers input field aliases, blank-cell handling, needs_review derivation,
citation verification against URLs the search tool actually returned, and a
minimal test-only profile that proves the pipeline is not tied to one domain.
strands is stubbed via conftest.
"""
import json

import pytest

import agent_prompt
import classifier_agent as ca
from taxonomies import ClassificationProfile, ContextField, get_profile

LAW = get_profile('law_enforcement')

# Test-only profile with a different input shape and output names.
TOY = ClassificationProfile(
    name='toy_tickets',
    title='Support ticket to category',
    taxonomy={'BILL': 'Billing', 'TECH': 'Technical issue', 'OTHER': 'Other'},
    fallback_code='OTHER',
    primary_field='ticket_text',
    context_fields=(ContextField('product', aliases=('sku',)),),
    code_field='category_code',
    description_field='category_name',
    expert='a support triage specialist',
    item='support ticket',
    taxonomy_label='CATEGORY',
    data_tag='ticket_data',
    search_tool_name='web_search_docs',
    search_tool_description='Search product documentation.',
)


class _ScriptedAgent:
    """Agent stub: returns a scripted response, records the query, and
    simulates the search tool by filling the deterministic tool record."""
    def __init__(self, response, searched_urls=(), profile=None):
        self._response = response
        self._urls = list(searched_urls)
        self.tool_call_counter = {'count': 0, 'urls': set()}
        self.queries = []
        if profile is not None:
            self.profile = profile

    def __call__(self, query):
        self.queries.append(query)
        self.tool_call_counter['count'] = len(self._urls)
        self.tool_call_counter['urls'].update(ca._normalize_url(u) for u in self._urls)
        return self._response


def _nibrs(**kw):
    body = {'code': '90D', 'confidence': 0.9, 'reasoning': 'DWI statute'}
    body.update(kw)
    return json.dumps(body)


# --- Input fields -----------------------------------------------------------

def test_statute_code_alias_maps_to_charge_code():
    # The blog example passes statute_code; it must reach the model as charge_code.
    agent = _ScriptedAgent(_nibrs())
    ca.classify_charge(agent, {'charge_text': 'DWI 2nd Offense', 'state': 'CA',
                               'statute_code': '23152'})
    assert 'charge_code: 23152' in agent.queries[0]


def test_blank_and_nan_context_cells_are_dropped():
    agent = _ScriptedAgent(_nibrs())
    ca.classify_charge(agent, {'charge_text': 'DWI', 'county': float('nan'), 'state': '  '})
    assert 'county' not in agent.queries[0]
    assert 'state' not in agent.queries[0]


# --- needs_review -----------------------------------------------------------

def test_high_confidence_does_not_need_review():
    out = ca.classify_charge(_ScriptedAgent(_nibrs(confidence=0.9)), {'charge_text': 'DWI'})
    assert out['needs_review'] is False


def test_low_confidence_needs_review():
    out = ca.classify_charge(_ScriptedAgent(_nibrs(confidence=0.4)), {'charge_text': 'DWI'})
    assert out['needs_review'] is True


def test_review_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv('REVIEW_THRESHOLD', '0.95')
    out = ca.classify_charge(_ScriptedAgent(_nibrs(confidence=0.9)), {'charge_text': 'DWI'})
    assert out['needs_review'] is True


def test_fallback_needs_review():
    out = ca.classify_charge(_ScriptedAgent('not json'), {'charge_text': 'x'})
    assert out['code'] == '90Z'
    assert out['needs_review'] is True


def test_needs_review_is_not_taken_from_model():
    out = ca.classify_charge(_ScriptedAgent(_nibrs(confidence=0.3, needs_review=False)),
                             {'charge_text': 'DWI'})
    assert out['needs_review'] is True


# --- Verified citations -----------------------------------------------------

def test_cited_url_that_was_retrieved_is_kept():
    url = 'https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?sectionNum=23152'
    agent = _ScriptedAgent(_nibrs(sources=[{'title': 'CA Veh Code 23152', 'url': url}]),
                           searched_urls=[url])
    out = ca.classify_charge(agent, {'charge_text': 'DWI'})
    assert out['sources'] == [{'title': 'CA Veh Code 23152', 'url': url}]


def test_trailing_slash_and_host_case_do_not_break_verification():
    agent = _ScriptedAgent(_nibrs(sources=[{'url': 'https://Example.gov/statute/'}]),
                           searched_urls=['https://example.gov/statute'])
    out = ca.classify_charge(agent, {'charge_text': 'DWI'})
    assert len(out['sources']) == 1


def test_cited_url_not_returned_by_search_is_dropped():
    agent = _ScriptedAgent(_nibrs(sources=[{'url': 'https://made-up.example/statute'}]),
                           searched_urls=['https://example.gov/real'])
    out = ca.classify_charge(agent, {'charge_text': 'DWI'})
    assert out['sources'] == []


def test_sources_dropped_when_no_search_ran():
    agent = _ScriptedAgent(_nibrs(sources=[{'url': 'https://example.gov/real'}]))
    out = ca.classify_charge(agent, {'charge_text': 'DWI'})
    assert out['sources'] == []
    assert out['web_grounded'] is False


def test_malformed_sources_do_not_fail_classification():
    bad = [{'url': 'javascript:alert(1)'}, 'not-a-dict', {'title': 'no url'},
           {'url': 'https://example.gov/ok'}]
    agent = _ScriptedAgent(_nibrs(sources=bad), searched_urls=['https://example.gov/ok'])
    out = ca.classify_charge(agent, {'charge_text': 'DWI'})
    assert out['code'] == '90D'
    assert [s['url'] for s in out['sources']] == ['https://example.gov/ok']


# --- A second profile -------------------------------------------------------

def test_other_profile_classifies_with_its_own_fields():
    body = json.dumps({'category_code': 'bill', 'confidence': 0.85, 'reasoning': 'refund'})
    agent = _ScriptedAgent(body, searched_urls=['https://example.com/docs'], profile=TOY)
    out = ca.classify_record(agent, {'ticket_text': 'Charged twice this month', 'sku': 'A1'})
    assert out['category_code'] == 'BILL'
    assert out['category_name'] == 'Billing'
    assert '<ticket_data>' in agent.queries[0]
    assert 'product: A1' in agent.queries[0]           # alias sku -> product
    assert 'code' not in out                         # uses its own field names


def test_other_profile_rejects_codes_from_other_taxonomies():
    agent = _ScriptedAgent(json.dumps({'category_code': '13A', 'confidence': 0.9}), profile=TOY)
    out = ca.classify_record(agent, {'ticket_text': 'x'})
    assert out['category_code'] == 'OTHER'
    assert out['needs_review'] is True


def test_every_profile_fallback_is_in_its_taxonomy():
    for profile in (LAW, TOY):
        fb = ca.fallback_result(profile)
        ca.build_result_model(profile)(**{k: v for k, v in fb.items() if k != 'needs_review'})


def test_unknown_taxonomy_is_rejected():
    with pytest.raises(ValueError):
        get_profile('not-a-domain')


# --- Prompt and agent wiring ------------------------------------------------

@pytest.mark.parametrize('profile', [LAW, TOY], ids=lambda p: p.name)
def test_prompt_is_generated_from_profile(profile):
    prompt = agent_prompt.get_classification_prompt(profile)
    assert all(code in prompt for code in profile.taxonomy)
    assert f'<{profile.data_tag}>' in prompt
    assert f'"{profile.code_field}"' in prompt
    assert '"sources"' in prompt
    assert profile.search_tool_name in prompt


def test_law_enforcement_prompt_uses_generic_output_fields():
    prompt = agent_prompt.get_classification_prompt(LAW)
    assert '"code"' in prompt and '"description"' in prompt
    assert '<charge_data>' in prompt


@pytest.mark.parametrize('profile', [LAW, TOY], ids=lambda p: p.name)
def test_agent_uses_profile_tool_and_records_retrieved_urls(profile):
    class _Search:
        def search(self, **kw):
            return {'results': [{'title': 't', 'url': 'https://example.gov/a/', 'content': 'c'}]}

    agent = ca.create_classification_agent(_Search(), profile)
    tool = agent.kwargs['tools'][0]
    assert tool.tool_name == profile.search_tool_name
    assert agent.profile is profile
    assert 'temperature' not in agent.kwargs['model'].kwargs
    assert agent.kwargs['callback_handler'] is None     # no model output in logs

    tool('some query')
    assert agent.tool_call_counter['count'] == 1
    assert 'https://example.gov/a' in agent.tool_call_counter['urls']
