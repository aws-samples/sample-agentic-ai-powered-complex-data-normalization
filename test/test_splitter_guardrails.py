# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Unit tests for the splitter cost controls.

Uses moto to mock S3 + SQS. Note: file-size and key-prefix filtering are now
enforced natively by the EventBridge rule (not tested here since that's
declarative config); these tests cover the app-level row-count cap and the
defense-in-depth prefix re-check that remain in code.
"""
import json
import importlib

import boto3
import pytest
from moto import mock_aws

REGION = 'us-east-1'
BUCKET = 'test-input-bucket'
QUEUE = 'test-charge-queue'


def _make_event(bucket, key):
    return {'detail': {'bucket': {'name': bucket}, 'object': {'key': key}}}


def _csv(rows):
    header = 'charge_text\n'
    return header + '\n'.join(f'charge {i}' for i in range(rows))


@pytest.fixture
def aws_env(monkeypatch):
    with mock_aws():
        s3 = boto3.client('s3', region_name=REGION)
        s3.create_bucket(Bucket=BUCKET)
        sqs = boto3.client('sqs', region_name=REGION)
        queue_url = sqs.create_queue(QueueName=QUEUE)['QueueUrl']
        monkeypatch.setenv('QUEUE_URL', queue_url)
        monkeypatch.setenv('MAX_ROWS', '100')
        monkeypatch.setenv('ALLOWED_PREFIX', 'inbox/')
        monkeypatch.setenv('BATCH_SIZE', '50')
        # Import fresh so module-level env/clients bind inside the mock.
        import lambda_splitter
        importlib.reload(lambda_splitter)
        yield lambda_splitter, s3, sqs, queue_url


def test_valid_upload_batches_to_sqs(aws_env):
    splitter, s3, sqs, queue_url = aws_env
    # 60 rows: under MAX_ROWS=100, spans 2 batches of 50.
    s3.put_object(Bucket=BUCKET, Key='inbox/charges.csv', Body=_csv(60))
    result = splitter.lambda_handler(_make_event(BUCKET, 'inbox/charges.csv'), None)
    assert result['statusCode'] == 200
    assert result['total_charges'] == 60
    assert result['total_batches'] == 2  # 60 rows / 50 per batch, rounded up
    attrs = sqs.get_queue_attributes(QueueUrl=queue_url,
                                     AttributeNames=['ApproximateNumberOfMessages'])
    assert int(attrs['Attributes']['ApproximateNumberOfMessages']) == 2


def test_row_count_over_limit_is_rejected(aws_env):
    splitter, s3, sqs, queue_url = aws_env
    s3.put_object(Bucket=BUCKET, Key='inbox/huge.csv', Body=_csv(200))  # > MAX_ROWS=100
    with pytest.raises(splitter.RejectedUpload):
        splitter.lambda_handler(_make_event(BUCKET, 'inbox/huge.csv'), None)
    # Nothing should have been enqueued.
    attrs = sqs.get_queue_attributes(QueueUrl=queue_url,
                                     AttributeNames=['ApproximateNumberOfMessages'])
    assert int(attrs['Attributes']['ApproximateNumberOfMessages']) == 0


def test_out_of_prefix_key_is_rejected(aws_env):
    splitter, s3, sqs, queue_url = aws_env
    s3.put_object(Bucket=BUCKET, Key='evil/charges.csv', Body=_csv(10))
    with pytest.raises(splitter.RejectedUpload):
        splitter.lambda_handler(_make_event(BUCKET, 'evil/charges.csv'), None)


def test_batch_message_shape(aws_env):
    splitter, s3, sqs, queue_url = aws_env
    s3.put_object(Bucket=BUCKET, Key='inbox/small.csv', Body=_csv(10))
    splitter.lambda_handler(_make_event(BUCKET, 'inbox/small.csv'), None)
    msg = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1)['Messages'][0]
    body = json.loads(msg['Body'])
    assert 'batch_id' in body and 'charges' in body
    assert body['total_batches'] == 1
    assert len(body['charges']) == 10
