import boto3
import os
import uuid
from datetime import datetime, timedelta

dynamodb = boto3.resource('dynamodb')
jobs_table = dynamodb.Table(os.environ['JOBS_TABLE_NAME'])

def handler(event, context):
    """Create job record in DynamoDB"""

    # Use provided job_id if available, otherwise generate new one
    job_id = event.get('job_id', str(uuid.uuid4()))
    timestamp = datetime.now().isoformat()
    ttl = int((datetime.now() + timedelta(hours=24)).timestamp())

    jobs_table.put_item(
        Item={
            'job_id': job_id,
            'status': 'processing',
            'filename': event['filename'],
            'url': event.get('url', ''),
            's3_key': event['s3_key'],
            'email': event['email'],
            'execution_arn': event.get('execution_arn', ''),
            'created_at': timestamp,
            'updated_at': timestamp,
            'ttl': ttl
        }
    )

    return {
        'job_id': job_id,
        'filename': event['filename'],
        'email': event['email']
    }
