# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Unit tests for the AgentCore Web Search client.

The HTTP layer is replaced with a fake, so no network or AWS calls are made.
Response shapes match live responses from the Web Search tool on an AgentCore
Gateway.
"""
import json

import pytest

import web_search as ws

GATEWAY = 'https://example-gw-abc123.gateway.bedrock-agentcore.us-east-1.amazonaws.com'
TOOL = 'web-search-tool___WebSearch'
LIVE_SHAPE = {'id': '1', 'results': [
    {'publishedDate': 'unknown', 'text': 'Sec. 49.04. DRIVING WHILE INTOXICATED.',
     'title': 'Texas Penal Code 49.04', 'url': 'https://statutes.example.gov/PE/49.04'}]}


class _Resp:
    def __init__(self, body, content_type='application/json'):
        self._body, self.headers = body, {'Content-Type': content_type}

    def read(self):
        return self._body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake_http(monkeypatch):
    """Record requests and answer tools/list and tools/call."""
    calls = []
    state = {'call_result': {'isError': False,
                             'content': [{'type': 'text', 'text': json.dumps(LIVE_SHAPE)}]},
             'content_type': 'application/json'}

    def urlopen(req, timeout=None):
        body = json.loads(req.data)
        calls.append({'body': body, 'headers': dict(req.headers), 'url': req.full_url})
        if body['method'] == 'tools/list':
            result = {'tools': [{'name': 'other___Thing'}, {'name': TOOL}]}
        else:
            result = state['call_result']
        payload = json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': result})
        if state['content_type'] == 'text/event-stream':
            payload = f'event: message\ndata: {payload}\n\n'
        return _Resp(payload, state['content_type'])

    monkeypatch.setattr(ws.urllib.request, 'urlopen', urlopen)
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'AKIDEXAMPLE')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'secret')
    monkeypatch.delenv('AWS_PROFILE', raising=False)
    return calls, state


def test_url_and_region_are_derived_from_gateway_url():
    c = ws.AgentCoreWebSearchClient(GATEWAY)
    assert c.url == GATEWAY + '/mcp'
    assert c.region == 'us-east-1'
    assert ws.AgentCoreWebSearchClient(GATEWAY + '/mcp/').url == GATEWAY + '/mcp'


def test_search_discovers_tool_and_maps_results(fake_http):
    calls, _ = fake_http
    out = ws.AgentCoreWebSearchClient(GATEWAY).search('texas dwi statute', max_results=3)
    assert out == {'results': [{'title': 'Texas Penal Code 49.04',
                                'url': 'https://statutes.example.gov/PE/49.04',
                                'content': 'Sec. 49.04. DRIVING WHILE INTOXICATED.'}]}
    assert [c['body']['method'] for c in calls] == ['tools/list', 'tools/call']
    assert calls[1]['body']['params'] == {
        'name': TOOL, 'arguments': {'query': 'texas dwi statute', 'maxResults': 3}}


def test_requests_are_sigv4_signed_for_agentcore(fake_http):
    calls, _ = fake_http
    ws.AgentCoreWebSearchClient(GATEWAY, tool_name=TOOL).search('q')
    auth = next(v for k, v in calls[0]['headers'].items() if k.lower() == 'authorization')
    assert auth.startswith('AWS4-HMAC-SHA256')
    assert '/us-east-1/bedrock-agentcore/aws4_request' in auth


def test_tool_name_is_cached(fake_http):
    calls, _ = fake_http
    c = ws.AgentCoreWebSearchClient(GATEWAY)
    c.search('a'); c.search('b')
    assert [x['body']['method'] for x in calls].count('tools/list') == 1


def test_query_and_max_results_are_bounded(fake_http):
    calls, _ = fake_http
    ws.AgentCoreWebSearchClient(GATEWAY, tool_name=TOOL).search('x' * 500, max_results=99)
    args = calls[0]['body']['params']['arguments']
    assert len(args['query']) == ws.MAX_QUERY_CHARS
    assert args['maxResults'] == ws.MAX_RESULTS_LIMIT


def test_event_stream_responses_are_parsed(fake_http):
    _, state = fake_http
    state['content_type'] = 'text/event-stream'
    out = ws.AgentCoreWebSearchClient(GATEWAY, tool_name=TOOL).search('q')
    assert out['results'][0]['url'] == 'https://statutes.example.gov/PE/49.04'


def test_structured_content_is_preferred(fake_http):
    _, state = fake_http
    state['call_result'] = {'isError': False, 'content': [], 'structuredContent': LIVE_SHAPE}
    out = ws.AgentCoreWebSearchClient(GATEWAY, tool_name=TOOL).search('q')
    assert len(out['results']) == 1


def test_tool_error_raises(fake_http):
    _, state = fake_http
    state['call_result'] = {'isError': True, 'content': [{'type': 'text', 'text': 'throttled'}]}
    with pytest.raises(ws.WebSearchError):
        ws.AgentCoreWebSearchClient(GATEWAY, tool_name=TOOL).search('q')


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://example-gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com',
                                 'https://untrusted.example.com/mcp'])
def test_non_aws_or_non_https_gateway_url_is_rejected(url):
    with pytest.raises(ValueError):
        ws.AgentCoreWebSearchClient(url)


def test_missing_gateway_url_gives_clear_error(monkeypatch):
    monkeypatch.delenv('WEB_SEARCH_GATEWAY_URL', raising=False)
    with pytest.raises(RuntimeError, match='WEB_SEARCH_GATEWAY_URL'):
        ws.create_search_client()
