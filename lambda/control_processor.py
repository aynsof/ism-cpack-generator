import json
import os
import boto3
from datetime import datetime
import uuid
import csv
from io import StringIO

# Configure boto3 clients
dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime', region_name='ap-southeast-2')

# Environment variables
CONTROLS_TABLE_NAME = os.environ['CONTROLS_TABLE_NAME']
CONFIG_MAPPINGS_TABLE_NAME = os.environ['CONFIG_MAPPINGS_TABLE_NAME']
BUCKET_NAME = os.environ['BUCKET_NAME']

# DynamoDB tables
controls_table = dynamodb.Table(CONTROLS_TABLE_NAME)
config_mappings_table = dynamodb.Table(CONFIG_MAPPINGS_TABLE_NAME)


def fetch_config_rules(s3_key):
    """Fetch AWS Config Rules content from S3"""
    try:
        response = s3_client.get_object(
            Bucket=BUCKET_NAME,
            Key=s3_key
        )
        return response['Body'].read().decode('utf-8')
    except Exception as e:
        print(f"Error fetching Config Rules from S3: {str(e)}")
        return None


def query_bedrock_for_mappings(control_id, prose, config_rules_content):
    """Query Bedrock to map ISM control to AWS Config Rules"""

    prompt = f"""Here is an ISM control in the format "control-id":"description":
"{control_id}":"{prose}"

First, consider whether it is relevant to workloads running in AWS. If not, return null.

If it is relevant to AWS, look through this list of AWS managed Config Rules: https://docs.aws.amazon.com/config/latest/developerguide/managed-rules-by-aws-config.html

Determine which Config Rules are relevant to this control. Think comprehensively and consider:

All AWS services that could be used as centralized logging facilities (e.g., CloudWatch, OpenSearch, Kinesis, S3, Redshift, MSK)
All AWS services that could transport or stream logs (e.g., Kinesis, MSK, load balancers)
All Config Rules related to encryption in transit, TLS, SSL, and HTTPS
Both direct and indirect relevance to the control

Do not stop at the first relevant rule. Return ALL Config Rules that could reasonably apply to this control, even if they address the control partially or in specific scenarios.

Respond in this format (return multiple rows as needed): ["control-id","description","config-rule-identifier","brief explanation of the relevance of this config rule to this ISM control"]

Do not provide any other explanatory text. Only return the data structure in the format above."""

    try:
        # Prepare Bedrock request
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.0  # Deterministic for consistency
        }

        # Invoke Bedrock using inference profile
        response = bedrock_runtime.invoke_model(
            modelId='global.anthropic.claude-opus-4-5-20251101-v1:0',
            contentType='application/json',
            accept='application/json',
            body=json.dumps(request_body)
        )

        # Parse response
        response_body = json.loads(response['body'].read())
        content = response_body['content'][0]['text']

        return content

    except Exception as e:
        print(f"Error querying Bedrock: {str(e)}")
        return None


def parse_bedrock_response(response_text, control_id, prose):
    """Parse Bedrock CSV response into list of mappings"""

    if not response_text:
        return []

    # Check for null response
    if response_text.strip().lower() in ['null', 'none', '']:
        print(f"Control {control_id} not relevant to AWS")
        return []

    mappings = []

    try:
        # Parse CSV format: ["control-id","description","config-rule-identifier","explanation"]
        # Handle both single-line and multi-line responses
        lines = response_text.strip().split('\n')

        for line in lines:
            line = line.strip()
            if not line or not line.startswith('['):
                continue

            # Parse JSON array format
            try:
                parts = json.loads(line)
                if len(parts) >= 4:
                    mappings.append({
                        'control_id': parts[0],
                        'control_description': parts[1],
                        'config_rule_identifier': parts[2],
                        'relevance_explanation': parts[3]
                    })
            except json.JSONDecodeError:
                # Fallback: try CSV parsing
                csv_reader = csv.reader(StringIO(line))
                for row in csv_reader:
                    if len(row) >= 4:
                        mappings.append({
                            'control_id': row[0].strip('"'),
                            'control_description': row[1].strip('"'),
                            'config_rule_identifier': row[2].strip('"'),
                            'relevance_explanation': row[3].strip('"')
                        })

    except Exception as e:
        print(f"Error parsing Bedrock response: {str(e)}")
        print(f"Response text: {response_text}")

    return mappings


def store_config_mappings(mappings, job_id, timestamp):
    """Store Config Rule mappings in DynamoDB"""

    stored_count = 0

    for mapping in mappings:
        try:
            config_mappings_table.put_item(
                Item={
                    'mapping_id': str(uuid.uuid4()),
                    'control_id': mapping['control_id'],
                    'control_description': mapping['control_description'],
                    'config_rule_identifier': mapping['config_rule_identifier'],
                    'relevance_explanation': mapping['relevance_explanation'],
                    'job_id': job_id,
                    'timestamp': timestamp,
                    'bedrock_model': 'claude-opus-4-5'
                }
            )
            stored_count += 1
            print(f"Stored mapping: {mapping['control_id']} -> {mapping['config_rule_identifier']}")

        except Exception as e:
            print(f"Error storing mapping: {str(e)}")

    return stored_count


def handler(event, context):
    """
    Process a single ISM control:
    1. Store control in ControlsTable
    2. Query Bedrock for AWS Config Rule mappings
    3. Store mappings in ConfigMappingsTable

    Expected event structure:
    {
        "control": {...},  # The control object
        "job_id": "uuid",
        "source_file": "filename.json",
        "s3_key": "uploads/...",
        "url": "optional url",
        "timestamp": "ISO timestamp",
        "config_rules_s3_key": "config-rules/{job_id}/rules.html"
    }
    """
    print(f"Control processor invoked: {json.dumps(event)}")

    try:
        control = event['control']
        job_id = event['job_id']
        source_file = event['source_file']
        s3_key = event['s3_key']
        url = event.get('url', '')
        timestamp = event.get('timestamp', datetime.now().isoformat())
        config_rules_s3_key = event.get('config_rules_s3_key')

        # Extract control ID
        control_id = control.get('id')
        if not control_id:
            error_msg = f"Control missing id field: {control}"
            print(error_msg)
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Control missing id field',
                    'control': control
                })
            }

        # Extract prose from parts
        prose = None
        parts = control.get('parts', [])
        for part in parts:
            if part.get('name') == 'statement':
                prose = part.get('prose', '')
                break

        if prose is None:
            error_msg = f"Control {control_id} missing prose statement"
            print(error_msg)
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Control missing prose statement',
                    'control_id': control_id
                })
            }

        # Store control in DynamoDB
        controls_table.put_item(
            Item={
                'id': control_id,
                'prose': prose,
                'job_id': job_id,
                'source_file': source_file,
                's3_key': s3_key,
                'url': url,
                'timestamp': timestamp,
                'title': control.get('title', ''),
                'class': control.get('class', '')
            }
        )

        print(f"Successfully stored control {control_id}")

        # Query Bedrock for Config Rule mappings
        mappings_stored = 0

        if config_rules_s3_key:
            print(f"Fetching Config Rules from S3: {config_rules_s3_key}")
            config_rules_content = fetch_config_rules(config_rules_s3_key)

            if config_rules_content:
                print(f"Querying Bedrock for control {control_id}")
                bedrock_response = query_bedrock_for_mappings(
                    control_id,
                    prose,
                    config_rules_content
                )

                if bedrock_response:
                    mappings = parse_bedrock_response(bedrock_response, control_id, prose)

                    if mappings:
                        mappings_stored = store_config_mappings(mappings, job_id, timestamp)
                        print(f"Stored {mappings_stored} Config Rule mappings for {control_id}")
                    else:
                        print(f"No Config Rule mappings found for {control_id}")
                else:
                    print(f"No response from Bedrock for {control_id}")
            else:
                print(f"Config Rules content not available, skipping Bedrock query")
        else:
            print(f"No Config Rules S3 key provided, skipping Bedrock query")

        return {
            'statusCode': 200,
            'body': json.dumps({
                'control_id': control_id,
                'message': 'Control stored successfully',
                'mappings_stored': mappings_stored
            })
        }

    except KeyError as e:
        error_msg = f"Missing required field: {str(e)}"
        print(error_msg)
        return {
            'statusCode': 400,
            'body': json.dumps({'error': error_msg})
        }

    except Exception as e:
        error_msg = f"Error processing control: {str(e)}"
        print(error_msg)
        import traceback
        print(traceback.format_exc())
        return {
            'statusCode': 500,
            'body': json.dumps({'error': error_msg})
        }
