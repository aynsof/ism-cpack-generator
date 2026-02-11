import boto3
import json
import os
from datetime import datetime

s3_client = boto3.client('s3')

def extract_controls_recursive(obj, controls_list):
    """Recursively extract all controls from nested JSON structure"""
    if isinstance(obj, dict):
        # If this object has a 'controls' key, process it
        if 'controls' in obj:
            for control in obj['controls']:
                controls_list.append(control)

        # Recursively search all values
        for value in obj.values():
            extract_controls_recursive(value, controls_list)

    elif isinstance(obj, list):
        # Recursively search all list items
        for item in obj:
            extract_controls_recursive(item, controls_list)

    return controls_list


def fetch_and_store_config_rules(job_id, bucket):
    """Fetch AWS Config Rules documentation and store in S3"""
    print("Fetching AWS Config Rules documentation...")
    config_rules_url = "https://docs.aws.amazon.com/config/latest/developerguide/managed-rules-by-aws-config.html"
    config_rules_key = f"config-rules/{job_id}/rules.html"

    try:
        import urllib3
        http = urllib3.PoolManager()
        response = http.request('GET', config_rules_url, timeout=30.0)

        if response.status != 200:
            print(f"Warning: Failed to fetch Config Rules (status {response.status}), continuing without them")
            return None
        else:
            config_rules_content = response.data

            # Store in S3
            s3_client.put_object(
                Bucket=bucket,
                Key=config_rules_key,
                Body=config_rules_content,
                ContentType='text/html'
            )
            print(f"Stored Config Rules at s3://{bucket}/{config_rules_key}")
            return config_rules_key

    except Exception as e:
        print(f"Warning: Error fetching Config Rules: {str(e)}, continuing without them")
        return None


def handler(event, context):
    """Download JSON from S3, extract controls, return list"""

    job_id = event['job_id']
    s3_key = event['s3_key']
    bucket = event['bucket_name']

    print(f"Processing JSON for job {job_id}")
    print(f"S3 location: s3://{bucket}/{s3_key}")

    # Download JSON from S3
    print(f"Downloading JSON from S3: {s3_key}")
    response = s3_client.get_object(Bucket=bucket, Key=s3_key)
    json_content = response['Body'].read().decode('utf-8')

    # Parse JSON
    print("Parsing JSON")
    data = json.loads(json_content)

    # Extract all controls recursively
    print("Extracting controls from JSON")
    controls = []
    extract_controls_recursive(data, controls)

    print(f"Found {len(controls)} controls in JSON")

    # Fetch Config Rules and store in S3
    config_rules_key = fetch_and_store_config_rules(job_id, bucket)

    # Format controls for Map state
    formatted_controls = []
    timestamp = datetime.now().isoformat()

    for control in controls:
        control_id = control.get('id')
        if not control_id:
            print(f"Skipping control without id: {control}")
            continue

        # Check if control has prose statement
        has_prose = False
        parts = control.get('parts', [])
        for part in parts:
            if part.get('name') == 'statement' and part.get('prose'):
                has_prose = True
                break

        if not has_prose:
            print(f"Skipping control {control_id} without prose statement")
            continue

        # Add to formatted list
        formatted_controls.append({
            'control': control,
            'job_id': job_id,
            'source_file': event.get('filename', ''),
            's3_key': s3_key,
            'url': event.get('url', ''),
            'config_rules_s3_key': config_rules_key,
            'timestamp': timestamp
        })

    print(f"Formatted {len(formatted_controls)} controls for processing")

    return {
        'controls': formatted_controls,
        'controls_count': len(formatted_controls)
    }
