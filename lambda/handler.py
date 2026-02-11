import json
import os
import boto3
from botocore.config import Config
from datetime import datetime
import uuid
from decimal import Decimal

# Configure S3 client with regional endpoint
s3_config = Config(
    signature_version='s3v4',
    s3={'addressing_style': 'path'}
)
s3_client = boto3.client('s3', config=s3_config)
BUCKET_NAME = os.environ['BUCKET_NAME']

# Configure DynamoDB clients
dynamodb = boto3.resource('dynamodb')
JOBS_TABLE_NAME = os.environ['JOBS_TABLE_NAME']
jobs_table = dynamodb.Table(JOBS_TABLE_NAME)


def decimal_to_number(obj):
    """Convert Decimal objects to int or float for JSON serialization"""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


def handler(event, context):
    """Main Lambda handler that routes requests to appropriate functions"""
    print(f"Event: {json.dumps(event)}")

    path = event.get('path', '')
    http_method = event.get('httpMethod', '')

    # CORS headers for all responses
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
    }

    try:
        # Handle preflight OPTIONS requests
        if http_method == 'OPTIONS':
            return {
                'statusCode': 200,
                'headers': headers,
                'body': ''
            }

        # Route to appropriate handler
        if path == '/upload-url' and http_method == 'POST':
            return get_upload_url(event, headers)
        elif path.startswith('/status/') and http_method == 'GET':
            return get_job_status(event, headers)
        else:
            return {
                'statusCode': 404,
                'headers': headers,
                'body': json.dumps({'error': 'Not found'})
            }

    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': str(e)})
        }


def get_upload_url(event, headers):
    """Generate presigned S3 URL for JSON upload"""
    try:
        # Parse request body if present
        body = {}
        if event.get('body'):
            body = json.loads(event['body'])

        # Generate unique filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        original_filename = body.get('filename', 'upload.json')
        # Sanitize filename
        safe_filename = original_filename.replace(' ', '-')
        key = f"uploads/{timestamp}-{unique_id}-{safe_filename}"

        # Generate presigned POST URL (valid for 5 minutes)
        presigned_post = s3_client.generate_presigned_post(
            Bucket=BUCKET_NAME,
            Key=key,
            Fields={
                'Content-Type': 'application/json'
            },
            Conditions=[
                {'Content-Type': 'application/json'},
                ['content-length-range', 1, 10485760]  # 1 byte to 10MB
            ],
            ExpiresIn=300  # 5 minutes
        )

        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({
                'uploadUrl': presigned_post['url'],
                'fields': presigned_post['fields'],
                'key': key
            })
        }

    except Exception as e:
        print(f"Error generating upload URL: {str(e)}")
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Failed to generate upload URL: {str(e)}'})
        }


def get_job_status(event, headers):
    """Get the status of a JSON processing job"""
    try:
        # Extract job_id from path
        path = event.get('path', '')
        path_params = event.get('pathParameters', {})
        job_id = path_params.get('job_id') if path_params else None

        if not job_id:
            # Try to extract from path manually
            parts = path.split('/')
            if len(parts) >= 3 and parts[1] == 'status':
                job_id = parts[2]

        if not job_id:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'Job ID is required'})
            }

        print(f"Checking status for job {job_id}")

        # Get job from DynamoDB
        response = jobs_table.get_item(Key={'job_id': job_id})

        if 'Item' not in response:
            return {
                'statusCode': 404,
                'headers': headers,
                'body': json.dumps({'error': 'Job not found'})
            }

        job = response['Item']
        status = job.get('status', 'unknown')

        result = {
            'job_id': job_id,
            'status': status,
            'filename': job.get('filename', ''),
            'created_at': job.get('created_at', '')
        }

        if status == 'completed':
            result['controls_dispatched'] = decimal_to_number(job.get('controls_dispatched', 0))
            result['completed_at'] = job.get('completed_at', '')
            result['message'] = 'Success!'
        elif status == 'failed':
            result['error'] = job.get('error_message', 'Unknown error')
            result['failed_at'] = job.get('failed_at', '')
        elif status == 'processing':
            result['message'] = 'In Progress...'

        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps(result)
        }

    except Exception as e:
        print(f"Error getting job status: {str(e)}")
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'})
        }
