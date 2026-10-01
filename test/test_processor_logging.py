# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Unit test for processor PII-safe logging.

Verifies that the per-charge log line contains the charge_id and NOT the
charge_text. Uses moto for DynamoDB and stubs the classification call so no
Bedrock/Strands invocation happens.
"""
import importlib

import boto3
import pytest
from moto import mock_aws

REGION = 'us-east-1'
TABLE = 'test-results'
SENSITIVE = 'DEFENDANT JOHN DOE AGGRAVATED ASSAULT WITH DEADLY WEAPON'


@pytest.fixture
def processor_env(monkeypatch):
    with mock_aws():
        ddb = boto3.resource('dynamodb', region_name=REGION)
        ddb.create_table(
            TableName=TABLE,
            KeySchema=[{'AttributeName': 'charge_id', 'KeyType': 'HASH'},
                       {'AttributeName': 'processing_timestamp', 'KeyType': 'RANGE'}],
            AttributeDefinitions=[{'AttributeName': 'charge_id', 'AttributeType': 'S'},
                                  {'AttributeName': 'processing_timestamp', 'AttributeType': 'S'}],
            BillingMode='PAY_PER_REQUEST',
        )
        monkeypatch.setenv('DYNAMODB_TABLE', TABLE)
        monkeypatch.setenv('AWS_DEFAULT_REGION', REGION)
        import lambda_processor
        importlib.reload(lambda_processor)

        # Stub the classification so we don't call Bedrock; return a valid result.
        def _fake_classify(charge_data):
            return {
                'code': '13A', 'description': 'Aggravated Assault',
                'confidence': 0.9, 'reasoning': 'stub', 'web_grounded': False,
                'input': charge_data,
            }
        monkeypatch.setattr(lambda_processor, 'classify_charge_lambda', _fake_classify)
        yield lambda_processor


def test_charge_text_not_logged(processor_env, capsys):
    processor = processor_env
    event = {'Records': [{'body': __import__('json').dumps({
        'batch_id': 'batch_000001',
        'charges': [{'charge_text': SENSITIVE, 'state': 'TX'}],
    })}]}
    processor.lambda_handler(event, None)
    out = capsys.readouterr().out
    # H1: the sensitive charge text must never appear in logs...
    assert SENSITIVE not in out
    assert 'DEFENDANT JOHN DOE' not in out
    # ...but the charge_id must, for traceability.
    assert 'batch_000001_01' in out


def test_item_persists_citations_review_flag_and_canonical_context(processor_env, monkeypatch):
    processor = processor_env

    def _fake_classify(charge_data):
        return {
            'code': '90D', 'description': 'Driving Under the Influence',
            'confidence': 0.5, 'reasoning': 'stub', 'web_grounded': True,
            'needs_review': True,
            'sources': [{'title': 'CA Veh Code 23152', 'url': 'https://example.gov/23152'}],
            'statute_reference': 'CA Veh Code 23152',
            'input': charge_data,
        }
    monkeypatch.setattr(processor, 'classify_charge_lambda', _fake_classify)

    event = {'Records': [{'body': __import__('json').dumps({
        'batch_id': 'batch_000002',
        'charges': [{'charge_text': 'DWI 2nd Offense', 'state': 'CA', 'statute_code': '23152'}],
    })}]}
    processor.lambda_handler(event, None)

    items = boto3.resource('dynamodb', region_name=REGION).Table(TABLE).scan()['Items']
    item = next(i for i in items if i['charge_id'] == 'batch_000002_01')
    assert item['needs_review'] is True
    assert item['sources'] == [{'title': 'CA Veh Code 23152', 'url': 'https://example.gov/23152'}]
    assert item['charge_code'] == '23152'          # statute_code alias, canonical name
    assert 'statute_code' not in item
    assert item['statute_reference'] == 'CA Veh Code 23152'
    assert item['taxonomy'] == 'law_enforcement'
