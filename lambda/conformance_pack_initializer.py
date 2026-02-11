"""
AWS Lambda Function: Conformance Pack Initializer

Step Functions Task 1: Initialize the conformance pack generation job
- Fetch AWS Config Rules documentation
- Query DynamoDB for unique Config Rules
- Split rules into batches for parallel processing
- Store documentation in S3 for batch processors

Event Format:
{
    "prefix": "ism-controls",  # Optional, default: "ism-controls"
    "job_id": "uuid",          # Optional, auto-generated if not provided
    "batch_size": 50           # Optional, number of rules per batch
}

Returns:
{
    "job_id": "uuid",
    "docs_s3_key": "s3-key-for-documentation",
    "batches": [
        {
            "batch_id": 1,
            "rules": [
                {"rule_id": "access-keys-rotated", "controls": ["ISM-0421", "ISM-0422"]},
                ...
            ]
        },
        ...
    ],
    "total_rules": 880,
    "total_batches": 18,
    "prefix": "ism-controls"
}

Environment Variables:
- CONFIG_MAPPINGS_TABLE_NAME: DynamoDB table with Config Rules mappings
- OUTPUT_BUCKET_NAME: S3 bucket for intermediate and final output
"""

import boto3
import json
import os
import uuid
from typing import Dict, List, Any
from collections import defaultdict
import urllib.request

# Initialize AWS clients
dynamodb = boto3.client('dynamodb')
s3_client = boto3.client('s3')

# Configuration from environment
CONFIG_MAPPINGS_TABLE = os.environ['CONFIG_MAPPINGS_TABLE_NAME']
OUTPUT_BUCKET = os.environ['OUTPUT_BUCKET_NAME']

# Constants
CONFIG_RULES_URL = 'https://docs.aws.amazon.com/config/latest/developerguide/managed-rules-by-aws-config.html'
DEFAULT_BATCH_SIZE = 50


def fetch_config_rules_documentation() -> str:
    """Fetch AWS Config Rules documentation from AWS docs"""
    print(f"Fetching AWS Config Rules documentation from {CONFIG_RULES_URL}...")

    try:
        with urllib.request.urlopen(CONFIG_RULES_URL, timeout=30) as response:
            content = response.read().decode('utf-8')
            print(f"✓ Fetched documentation ({len(content)} bytes)")
            return content
    except Exception as e:
        print(f"✗ Error fetching documentation: {e}")
        raise


def get_unique_config_rules(job_id: str) -> Dict[str, List[str]]:
    """
    Scan DynamoDB ConfigMappingsTable and extract unique Config Rules for a specific job
    Returns dict: {rule_identifier: [control_id1, control_id2, ...]}
    """
    print(f"\nScanning DynamoDB table: {CONFIG_MAPPINGS_TABLE} for job_id: {job_id}...")

    rules_to_controls = defaultdict(list)

    try:
        paginator = dynamodb.get_paginator('scan')
        page_iterator = paginator.paginate(TableName=CONFIG_MAPPINGS_TABLE)

        item_count = 0
        filtered_count = 0
        for page in page_iterator:
            for item in page.get('Items', []):
                item_count += 1

                # Filter by job_id to only process mappings for current job
                item_job_id = item.get('job_id', {}).get('S', '')
                if item_job_id != job_id:
                    continue

                filtered_count += 1
                rule_id = item.get('config_rule_identifier', {}).get('S', '')
                control_id = item.get('control_id', {}).get('S', '')

                if rule_id and control_id:
                    rules_to_controls[rule_id].append(control_id)

        unique_rules = len(rules_to_controls)
        print(f"✓ Scanned {item_count} total mappings, found {filtered_count} for job {job_id}")
        print(f"✓ Identified {unique_rules} unique Config Rules")

        return dict(rules_to_controls)

    except Exception as e:
        print(f"✗ Error scanning DynamoDB: {e}")
        raise


def upload_documentation_to_s3(docs_content: str, job_id: str) -> str:
    """Upload documentation to S3 for batch processors to use"""
    s3_key = f"conformance-packs/{job_id}/docs/config-rules-documentation.html"

    try:
        s3_client.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=s3_key,
            Body=docs_content.encode('utf-8'),
            ContentType='text/html'
        )
        print(f"✓ Uploaded documentation to s3://{OUTPUT_BUCKET}/{s3_key}")
        return s3_key
    except Exception as e:
        print(f"✗ Error uploading documentation: {e}")
        raise


def split_into_batches(rules_to_controls: Dict[str, List[str]],
                       batch_size: int = DEFAULT_BATCH_SIZE) -> List[Dict[str, Any]]:
    """Split rules into batches for parallel processing"""
    batches = []
    current_batch = []
    batch_id = 1

    for rule_id, controls in rules_to_controls.items():
        current_batch.append({
            "rule_id": rule_id,
            "controls": controls
        })

        if len(current_batch) >= batch_size:
            batches.append({
                "batch_id": batch_id,
                "rules": current_batch
            })
            batch_id += 1
            current_batch = []

    # Add remaining rules
    if current_batch:
        batches.append({
            "batch_id": batch_id,
            "rules": current_batch
        })

    print(f"✓ Split {len(rules_to_controls)} rules into {len(batches)} batches")
    return batches


def lambda_handler(event, context):
    """
    Lambda handler for conformance pack initialization
    """
    try:
        print("=" * 80)
        print("Conformance Pack Initializer")
        print("=" * 80)

        # Parse event - job_id is now REQUIRED
        job_id = event.get('job_id')
        if not job_id:
            return {
                'statusCode': 400,
                'error': 'job_id is required'
            }

        prefix = event.get('prefix', 'ism-controls')
        batch_size = event.get('batch_size', DEFAULT_BATCH_SIZE)

        print(f"\nJob ID: {job_id}")
        print(f"Prefix: {prefix}")
        print(f"Batch Size: {batch_size}")
        print(f"Output Bucket: {OUTPUT_BUCKET}")

        # Step 1: Fetch documentation
        print("\n" + "-" * 80)
        docs_content = fetch_config_rules_documentation()

        # Step 2: Upload documentation to S3
        print("-" * 80)
        docs_s3_key = upload_documentation_to_s3(docs_content, job_id)

        # Step 3: Get unique Config Rules from DynamoDB for this job
        print("-" * 80)
        rules_to_controls = get_unique_config_rules(job_id)

        if not rules_to_controls:
            return {
                'statusCode': 400,
                'error': 'No Config Rules found in DynamoDB table',
                'job_id': job_id
            }

        # Step 4: Split into batches
        print("-" * 80)
        batches = split_into_batches(rules_to_controls, batch_size)

        print("\n" + "=" * 80)
        print("✓ INITIALIZATION COMPLETE")
        print("=" * 80)
        print(f"Total Rules: {len(rules_to_controls)}")
        print(f"Total Batches: {len(batches)}")

        # Return result for Step Functions
        return {
            'statusCode': 200,
            'job_id': job_id,
            'docs_s3_key': docs_s3_key,
            'batches': batches,
            'total_rules': len(rules_to_controls),
            'total_batches': len(batches),
            'prefix': prefix
        }

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        print(traceback.format_exc())

        return {
            'statusCode': 500,
            'error': str(e),
            'job_id': event.get('job_id', 'unknown')
        }
