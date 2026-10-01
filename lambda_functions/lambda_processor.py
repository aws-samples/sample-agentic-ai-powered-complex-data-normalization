#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Lambda Processor - Classifies records using Strands agent
OTEL instrumentation happens automatically via Strands SDK
"""

import json
import os
import boto3
from datetime import datetime, UTC
from decimal import Decimal
from classifier_agent import create_classification_agent, classify_charge
from web_search import create_search_client
from taxonomies import get_profile

PROFILE = get_profile()

# Initialize OTEL for Strands (only if observability enabled)
if os.environ.get('AGENT_OBSERVABILITY_ENABLED', 'false').lower() == 'true':
    from strands.telemetry import StrandsTelemetry
    
    # Setup OTLP exporter for CloudWatch
    StrandsTelemetry().setup_otlp_exporter(
        endpoint=os.environ.get('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://localhost:4318/v1/traces')
    )

# OTEL auto-instrumentation via Strands
# No manual OTEL setup needed - Strands handles it when AGENT_OBSERVABILITY_ENABLED=true


def convert_floats_to_decimal(obj):
    """Recursively convert all floats in nested dict/list to Decimal for DynamoDB"""
    if isinstance(obj, list):
        return [convert_floats_to_decimal(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: convert_floats_to_decimal(value) for key, value in obj.items()}
    elif isinstance(obj, float):
        return Decimal(str(obj))
    else:
        return obj

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ.get('DYNAMODB_TABLE', 'test-table'))

# Global agent (reused across invocations)
agent = None


def get_agent():
    """Get or create agent (cached across invocations)"""
    global agent
    
    if agent is None:
        agent = create_classification_agent(create_search_client(), PROFILE)
    
    return agent


def classify_charge_lambda(charge_data: dict) -> dict:
    """Classify a single charge - Lambda wrapper"""
    agent = get_agent()
    return classify_charge(agent, charge_data)


def lambda_handler(event, context):
    """
    Triggered by SQS message containing batch of charges
    Processes all charges and writes to DynamoDB
    
    OTEL tracing is automatic via Strands SDK when AGENT_OBSERVABILITY_ENABLED=true
    """
    
    # Set session context for trace correlation (optional)
    from opentelemetry import baggage, context as otel_context
    
    # Parse SQS message
    for record in event['Records']:
        message = json.loads(record['body'])
        
        batch_id = message['batch_id']
        charges = message['charges']
        session_id = message.get('session_id', batch_id)
        
        # Attach session ID to OTEL baggage for trace correlation
        if os.environ.get('AGENT_OBSERVABILITY_ENABLED', 'false').lower() == 'true':
            ctx = baggage.set_baggage("session.id", session_id)
            otel_context.attach(ctx)
        
        print(f"Processing {batch_id}: {len(charges)} records")
        
        # Process each charge in the batch
        results = []
        for i, charge_data in enumerate(charges, 1):
            # Handle both string (legacy) and dict (new) formats
            if isinstance(charge_data, str):
                charge_data = {PROFILE.primary_field: charge_data}

            charge_id = f"{batch_id}_{i:02d}"
            # Log the record ID only. Never log the input text: records such as
            # criminal charges are sensitive and must not land in CloudWatch.
            print(f"  {i}/{len(charges)}: {charge_id}")

            result = classify_charge_lambda(charge_data)
            result['batch_id'] = batch_id
            result['charge_id'] = charge_id

            results.append(result)
        
        # Write results to DynamoDB in batch
        with table.batch_writer() as batch_writer:
            for result in results:
                # Build DynamoDB item with all fields
                input_data = result.get('input', {})
                
                item = {
                    'charge_id': result['charge_id'],
                    'processing_timestamp': result.get('processing_timestamp', datetime.now(UTC).isoformat()),
                    PROFILE.primary_field: input_data.get(PROFILE.primary_field, ''),
                    PROFILE.code_field: result[PROFILE.code_field],
                    PROFILE.description_field: result[PROFILE.description_field],
                    'confidence': Decimal(str(result['confidence'])),
                    'reasoning': result.get('reasoning', ''),
                    'sources': result.get('sources', []),
                    'web_grounded': result.get('web_grounded', False),
                    'needs_review': result.get('needs_review', False),
                    'taxonomy': PROFILE.name,
                    'batch_id': result['batch_id']
                }

                # Add context fields if present (flattened, canonical names;
                # aliases such as statute_code map onto charge_code)
                item.update(PROFILE.resolve_context(input_data))

                # Add optional domain-specific outputs
                for name in PROFILE.extra_output_fields:
                    if result.get(name):
                        item[name] = result[name]
                if 'error' in result:
                    item['error'] = result['error']
                
                # Convert any remaining floats to Decimal (safety check)
                item = convert_floats_to_decimal(item)
                
                batch_writer.put_item(Item=item)
        
        print(f"{batch_id} complete: {len(results)} records classified")
    
    return {
        'statusCode': 200,
        'processed': len(results)
    }
