#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Lambda Splitter - Reads CSV from S3 and sends batches to SQS
"""

import json
import os
import boto3
import pandas as pd
from io import StringIO
from taxonomies import get_profile

PROFILE = get_profile()

s3 = boto3.client('s3')
sqs = boto3.client('sqs')

BATCH_SIZE = int(os.environ.get('BATCH_SIZE', '25'))
QUEUE_URL = None  # Set via environment variable

# Cost control. One upload can trigger many model calls, so the row count per
# upload is capped. File SIZE and key PREFIX are enforced
# natively by the EventBridge rule (detail.object.size / key prefix) so the
# function is never even invoked for an oversized or out-of-prefix object; the
# checks below are defense-in-depth.
MAX_ROWS = int(os.environ.get('MAX_ROWS', '100000'))  # 100K charges
ALLOWED_PREFIX = os.environ.get('ALLOWED_PREFIX', 'inbox/')


class RejectedUpload(Exception):
    """Raised when an upload violates the ingest budget/guardrails."""


def lambda_handler(event, context):
    """
    Triggered by S3 upload via EventBridge
    Reads CSV, splits into batches, sends to SQS
    """

    # Get S3 details from EventBridge event
    bucket = event['detail']['bucket']['name']
    key = event['detail']['object']['key']

    print(f"Processing CSV: s3://{bucket}/{key}")

    # Defense-in-depth: key prefix. The EventBridge rule already filters on this,
    # but re-check in case the function is invoked by any other path.
    if ALLOWED_PREFIX and not key.startswith(ALLOWED_PREFIX):
        raise RejectedUpload(
            f"Object key does not start with required prefix {ALLOWED_PREFIX!r}; skipping."
        )

    # Read CSV from S3
    response = s3.get_object(Bucket=bucket, Key=key)
    csv_content = response['Body'].read().decode('utf-8')

    # Parse CSV
    df = pd.read_csv(StringIO(csv_content))
    
    # Expected columns: the profile's primary field plus any context fields
    # (for law_enforcement: charge_text, state, county, charge_code, offense_type,
    # date_of_offense). A single-column file is treated as primary-field values.
    if PROFILE.primary_field in df.columns:
        # New format with full context
        charge_records = df.to_dict('records')
    else:
        # Legacy format - first column is charge text
        charge_column = df.columns[0]
        charges = df[charge_column].dropna().unique().tolist()
        charges = [c for c in charges if str(c).strip() and str(c) != 'count']
        charge_records = [{PROFILE.primary_field: c} for c in charges]
    
    print(f"Found {len(charge_records)} charges")

    # Guardrail 3: row count. Reject files that would fan out beyond the per-upload
    # budget, bounding total Bedrock spend from a single PutObject.
    if len(charge_records) > MAX_ROWS:
        raise RejectedUpload(
            f"Row count {len(charge_records)} exceeds limit {MAX_ROWS}; rejected."
        )

    # Split into batches of BATCH_SIZE records
    batches = [charge_records[i:i+BATCH_SIZE] for i in range(0, len(charge_records), BATCH_SIZE)]
    
    print(f"Created {len(batches)} batches of {BATCH_SIZE} charges")
    
    # Send each batch to SQS
    queue_url = os.environ.get('QUEUE_URL') or QUEUE_URL
    
    for i, batch in enumerate(batches):
        message = {
            'batch_id': f'batch_{i+1:06d}',
            'charges': batch,
            'source_file': f's3://{bucket}/{key}',
            'batch_number': i + 1,
            'total_batches': len(batches)
        }
        
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message)
        )
        
        if (i + 1) % 100 == 0:
            print(f"Sent {i+1}/{len(batches)} batches to SQS")
    
    print(f"All {len(batches)} batches sent to SQS")
    
    return {
        'statusCode': 200,
        'total_charges': len(charge_records),
        'total_batches': len(batches),
        'batch_size': BATCH_SIZE,
        'queue_url': queue_url
    }
