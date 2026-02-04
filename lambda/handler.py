import json
import os
import boto3
from botocore.config import Config
from datetime import datetime
import uuid

# Configure S3 client with regional endpoint
s3_config = Config(
    signature_version='s3v4',
    s3={'addressing_style': 'path'}
)
s3_client = boto3.client('s3', config=s3_config)
BUCKET_NAME = os.environ['BUCKET_NAME']


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
        elif path == '/submit' and http_method == 'POST':
            return submit_form(event, headers)
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
    """Generate presigned S3 URL for PDF upload"""
    try:
        # Parse request body if present
        body = {}
        if event.get('body'):
            body = json.loads(event['body'])

        # Generate unique filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        original_filename = body.get('filename', 'upload.pdf')
        # Sanitize filename
        safe_filename = original_filename.replace(' ', '-')
        key = f"uploads/{timestamp}-{unique_id}-{safe_filename}"

        # Generate presigned POST URL (valid for 5 minutes)
        presigned_post = s3_client.generate_presigned_post(
            Bucket=BUCKET_NAME,
            Key=key,
            Fields={
                'Content-Type': 'application/pdf'
            },
            Conditions=[
                {'Content-Type': 'application/pdf'},
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
        raise


def submit_form(event, headers):
    """Process form submission and return filename"""
    try:
        # Parse request body
        body = json.loads(event['body'])

        filename = body.get('filename', '')
        regex = body.get('regex', '')
        url = body.get('url', '')

        # Validate inputs
        if not filename:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'Filename is required'})
            }

        # Log the submission
        print(f"Form submitted - Filename: {filename}, Regex: {regex}, URL: {url}")

        # Return success with filename
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({
                'filename': filename,
                'status': 'success'
            })
        }

    except Exception as e:
        print(f"Error processing form: {str(e)}")
        raise
