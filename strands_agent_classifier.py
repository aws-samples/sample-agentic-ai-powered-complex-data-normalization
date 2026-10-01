#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Criminal Charge Classification using Strands Agent
Supports single charge and batch CSV processing with web search
"""

import json
import os
import sys
import argparse
import pandas as pd
from typing import Dict, List
from datetime import datetime

# Add lambda_functions to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lambda_functions'))

from classifier_agent import (HUMAN_REVIEW_NOTICE, classify_charge,
                              create_classification_agent, guardrail_enabled)
from taxonomies import get_profile
from web_search import create_search_client

# Set from --show-reasoning in main().
SHOW_REASONING = False




def classify_single_charge_cli(agent, charge_data: Dict) -> Dict:
    """Classify a single charge with CLI console output"""
    
    charge_text = charge_data.get('charge_text', '')
    state = charge_data.get('state', '')
    county = charge_data.get('county', '')
    charge_code = charge_data.get('charge_code', '')
    offense_type = charge_data.get('offense_type', '')
    
    print(f"\n{'='*70}")
    print(f"Processing: {charge_text}")
    if county or state:
        print(f"Location: {county}{', ' if county and state else ''}{state}")
    if charge_code:
        print(f"Code: {charge_code}", end='')
    if offense_type:
        print(f" | Type: {offense_type}", end='')
    print()
    print(f"{'='*70}")
    print("\n    🤖 Agent analyzing charge...")
    
    # Use shared classification logic
    result = classify_charge(agent, charge_data)
    
    # Display result
    if 'error' not in result:
        print("\n    ✅ Classification Complete!")
        print(f"    NIBRS Code: {result['code']} - {result['description']}")
        print(f"    Confidence: {result['confidence']*100:.0f}%")
        if result.get('statute_reference'):
            print(f"    Statute: {result['statute_reference']}")
        print(f"    Web Grounded: {'Yes' if result.get('web_grounded') else 'No'}")
        print(f"    Needs Review: {'Yes' if result.get('needs_review') else 'No'}")
        for source in result.get('sources', []):
            print(f"    Source: {source['url']}")
        # Reasoning can restate the input record, so it is shown only on request.
        if SHOW_REASONING:
            print(f"\n    Reasoning: {result['reasoning'][:200]}...")
    else:
        print(f"    ❌ Error: {result.get('error', 'Unknown error')}")
    
    return result


def process_csv_batch(agent, csv_file: str, max_charges: int = 10) -> List[Dict]:
    """Process charges from CSV file"""
    
    print(f"\n📊 Reading CSV: {csv_file}")
    df = pd.read_csv(csv_file)
    
    # Assume first column is charge text
    charge_column = df.columns[0]
    
    # Get unique charges
    charges = df[charge_column].dropna().unique().tolist()
    charges = [c for c in charges if str(c).strip() and str(c) != 'count']
    
    if len(charges) > max_charges:
        print(f"⚠️  Limiting to {max_charges} charges for demo")
        charges = charges[:max_charges]
    
    print(f"✅ Found {len(charges)} unique charges to process\n")
    
    results = []
    for i, charge_text in enumerate(charges, 1):
        charge_data = {
            'charge_id': f'charge_{i:03d}',
            'charge_text': charge_text,
            # Add more fields if available in CSV
        }
        
        
        result = classify_single_charge_cli(agent, charge_data)
        result['charge_id'] = charge_data['charge_id']
        results.append(result)
    
    return results


def main():
    parser = argparse.ArgumentParser(description='Criminal Charge Classification with Strands Agent')
    parser.add_argument('--charge', type=str, help='Single charge text to classify')
    parser.add_argument('--state', type=str, help='State (e.g., TX, CA)')
    parser.add_argument('--county', type=str, help='County name')
    parser.add_argument('--code', type=str, help='Charge code')
    parser.add_argument('--type', type=str, help='Offense type (Felony/Misdemeanor)')
    parser.add_argument('--date', type=str, help='Date of offense')
    parser.add_argument('--csv', type=str, help='CSV file to process')
    parser.add_argument('--max', type=int, default=10, help='Max charges from CSV')
    parser.add_argument('--show-reasoning', action='store_true',
                        help='Print the agent reasoning (can restate the input record)')
    
    args = parser.parse_args()
    global SHOW_REASONING
    SHOW_REASONING = args.show_reasoning
    
    print("\n" + "="*70)
    print("🚀 STRANDS AGENT - CRIMINAL CHARGE CLASSIFICATION")
    print("="*70)
    print("\nFeatures:")
    print("  ✓ Agentic reasoning with tool use")
    print("  ✓ Web search for statute lookups (Web Search on Amazon Bedrock AgentCore)")
    print("  ✓ Jurisdiction-aware classification")
    print("  ✓ Supports single charge or batch CSV")
    print(f"\n⚠️  {HUMAN_REVIEW_NOTICE}")
    if not guardrail_enabled():
        print("⚠️  GUARDRAIL_ID is not set: input screening (prompt-attack blocking and PII "
              "masking) is OFF for this run. See the README to enable it.")
    print()
    
    # Create agent
    print("🔧 Creating Strands agent...")
    # The CLI's flags are built for the law enforcement worked example.
    agent = create_classification_agent(create_search_client(), get_profile('law_enforcement'))
    print("✅ Agent ready!\n")
    
    results = []
    
    if args.charge:
        # Single charge classification
        charge_data = {
            'charge_text': args.charge,
            'state': args.state,
            'county': args.county,
            'charge_code': args.code,
            'offense_type': args.type,
            'date_of_offense': args.date
        }
        result = classify_single_charge_cli(agent, charge_data)
        results = [result]
        
    elif args.csv:
        # Batch CSV processing
        results = process_csv_batch(agent, args.csv, args.max)
        
    else:
        # Demo with sample charges
        print("📝 Running demo with sample charges...\n")
        demo_charges = [
            {
                "charge_text": "DWI 2nd Offense",
                "state": "TX",
                "county": "Travis County",
                "charge_code": "49.04",
                "offense_type": "Misdemeanor"
            },
            {
                "charge_text": "POSS CS PG 1 <1G",
                "state": "TX",
                "county": "Harris County",
                "charge_code": "481.115",
                "offense_type": "Felony"
            },
            {
                "charge_text": "BURGLARY OF HABITATION",
                "state": "TX",
                "county": "Dallas County",
                "charge_code": "30.02",
                "offense_type": "Felony"
            }
        ]
        
        
        for charge in demo_charges:
            result = classify_single_charge_cli(agent, charge)
            results.append(result)
    
    # Summary
    if results:
        print("\n" + "="*70)
        print("📊 SUMMARY")
        print("="*70)
        print(f"\nTotal Charges: {len(results)}")
        avg_conf = sum(r.get('confidence', 0) for r in results) / len(results)
        print(f"Average Confidence: {avg_conf*100:.1f}%")
        web_grounded = sum(1 for r in results if r.get('web_grounded'))
        print(f"Web-Grounded: {web_grounded}/{len(results)}")
        
        # Save results
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f"strands_results_{timestamp}.json"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({
                'timestamp': datetime.utcnow().isoformat(),
                'architecture': 'strands_agent_with_web_search',
                'model': 'claude-sonnet-5',
                'results': results
            }, f, indent=2)
        
        print(f"\n💾 Results saved to: {output_file}")
        print()


if __name__ == '__main__':
    main()
