# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Web search client for the Web Search tool on Amazon Bedrock AgentCore.

The Web Search tool is a managed connector on an AgentCore Gateway, exposed as a
Model Context Protocol (MCP) tool. This client calls the gateway's MCP endpoint
with SigV4-signed JSON-RPC requests, so the only dependency is botocore and no
search API key is needed. Queries are served within AWS.

The client returns results in the shape the classification agent's search tool
expects: {"results": [{"title", "url", "content"}]}.
"""

import json
import os
import urllib.request
from typing import Dict, Optional
from urllib.parse import urlsplit

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

SIGNING_SERVICE = 'bedrock-agentcore'
TOOL_SUFFIX = '___WebSearch'
MAX_QUERY_CHARS = 200     # Web Search tool query limit
MAX_RESULTS_LIMIT = 25    # Web Search tool maxResults upper bound


class WebSearchError(Exception):
    """Raised when the gateway returns an error for a search request."""


def _region_from_url(url: str) -> Optional[str]:
    # https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
    parts = urlsplit(url).netloc.split('.')
    if 'bedrock-agentcore' in parts:
        idx = parts.index('bedrock-agentcore')
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def _parse_body(content_type: str, body: str) -> Dict:
    """Parse a JSON-RPC response, whether returned as JSON or as an SSE stream."""
    if 'text/event-stream' in (content_type or ''):
        data = [line[5:].strip() for line in body.splitlines() if line.startswith('data:')]
        body = data[-1] if data else '{}'
    return json.loads(body)


class AgentCoreWebSearchClient:
    """Calls the Web Search tool through an AgentCore Gateway with IAM auth."""

    def __init__(self, gateway_url: str, region: str = None, tool_name: str = None,
                 timeout: int = 30):
        parts = urlsplit(gateway_url)
        if parts.scheme != 'https' or not parts.netloc.endswith('.amazonaws.com'):
            raise ValueError('Gateway URL must be an https://...amazonaws.com AgentCore Gateway endpoint.')
        url = gateway_url.rstrip('/')
        self.url = url if url.endswith('/mcp') else url + '/mcp'
        self.region = region or _region_from_url(self.url) or os.environ.get('AWS_REGION')
        self.timeout = timeout
        self._tool_name = tool_name
        self._session = boto3.Session()
        self._request_id = 0

    def _rpc(self, method: str, params: Dict = None) -> Dict:
        self._request_id += 1
        payload = {'jsonrpc': '2.0', 'id': self._request_id, 'method': method}
        if params is not None:
            payload['params'] = params
        request = AWSRequest(
            method='POST', url=self.url, data=json.dumps(payload),
            headers={'Content-Type': 'application/json',
                     'Accept': 'application/json, text/event-stream'})
        # Credentials are fetched per request so rotated Lambda credentials are used.
        credentials = self._session.get_credentials().get_frozen_credentials()
        SigV4Auth(credentials, SIGNING_SERVICE, self.region).add_auth(request)
        http_request = urllib.request.Request(
            self.url, data=request.body.encode() if isinstance(request.body, str) else request.body,
            headers=dict(request.headers), method='POST')
        # Scheme and host are validated in __init__ (https, *.amazonaws.com only).
        with urllib.request.urlopen(http_request, timeout=self.timeout) as response:  # nosec B310 # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            message = _parse_body(response.headers.get('Content-Type', ''),
                                  response.read().decode('utf-8'))
        if 'error' in message:
            raise WebSearchError(f"{method} failed: {message['error'].get('message', 'error')}")
        return message.get('result', {})

    @property
    def tool_name(self) -> str:
        """The gateway exposes the tool as '<target name>___WebSearch'."""
        if self._tool_name is None:
            tools = self._rpc('tools/list').get('tools', [])
            matches = [t['name'] for t in tools if t['name'].endswith(TOOL_SUFFIX)]
            if not matches:
                raise WebSearchError('No Web Search tool found on the gateway.')
            self._tool_name = matches[0]
        return self._tool_name

    def search(self, query: str, max_results: int = 3, **_ignored) -> Dict:
        """Run one web search. Returns {"results": [{"title", "url", "content"}]}."""
        arguments = {
            'query': query[:MAX_QUERY_CHARS],
            'maxResults': max(1, min(int(max_results), MAX_RESULTS_LIMIT)),
        }
        result = self._rpc('tools/call', {'name': self.tool_name, 'arguments': arguments})
        if result.get('isError'):
            text = ''.join(c.get('text', '') for c in result.get('content', []))
            raise WebSearchError(f'Web Search returned an error: {text[:200]}')

        payload = result.get('structuredContent')
        if not payload:
            text = next((c.get('text') for c in result.get('content', [])
                         if c.get('type') == 'text'), '{}')
            payload = json.loads(text)
        return {'results': [
            {'title': r.get('title', ''), 'url': r.get('url', ''), 'content': r.get('text', '')}
            for r in payload.get('results', [])
        ]}


def create_search_client() -> AgentCoreWebSearchClient:
    """Build the client from the WEB_SEARCH_GATEWAY_URL environment variable."""
    url = os.environ.get('WEB_SEARCH_GATEWAY_URL')
    if not url:
        raise RuntimeError(
            'WEB_SEARCH_GATEWAY_URL is not set. Deploy the stack and use its '
            'WebSearchGatewayUrl output (see README).')
    return AgentCoreWebSearchClient(url)
