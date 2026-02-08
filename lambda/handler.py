import json
import os
import boto3
from botocore.config import Config
from datetime import datetime, timedelta
import uuid
from decimal import Decimal

# Configure S3 client with regional endpoint
s3_config = Config(
    signature_version='s3v4',
    s3={'addressing_style': 'path'}
)
s3_client = boto3.client('s3', config=s3_config)
lambda_client = boto3.client('lambda')
BUCKET_NAME = os.environ['BUCKET_NAME']
CONTROL_PROCESSOR_FUNCTION_NAME = os.environ.get('CONTROL_PROCESSOR_FUNCTION_NAME')

# Configure DynamoDB clients
dynamodb = boto3.resource('dynamodb')
CONTROLS_TABLE_NAME = os.environ['CONTROLS_TABLE_NAME']
JOBS_TABLE_NAME = os.environ['JOBS_TABLE_NAME']
controls_table = dynamodb.Table(CONTROLS_TABLE_NAME)
jobs_table = dynamodb.Table(JOBS_TABLE_NAME)


def decimal_to_number(obj):
    """Convert Decimal objects to int or float for JSON serialization"""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


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


def handler(event, context):
    """Main Lambda handler that routes requests to appropriate functions"""
    print(f"Event: {json.dumps(event)}")

    # Check if this is an async invocation for processing
    if event.get('async_processing'):
        return process_json_job(event)

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


def submit_form(event, headers):
    """Create a job and trigger async JSON processing"""
    try:
        # Parse request body
        body = json.loads(event['body'])

        filename = body.get('filename', '')
        url = body.get('url', '')
        s3_key = body.get('s3_key', '')

        # Validate inputs
        if not filename:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'Filename is required'})
            }

        if not s3_key:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'S3 key is required'})
            }

        # Generate job ID
        job_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()
        ttl = int((datetime.now() + timedelta(hours=24)).timestamp())

        # Create job record in DynamoDB
        jobs_table.put_item(
            Item={
                'job_id': job_id,
                'status': 'processing',
                'filename': filename,
                'url': url,
                's3_key': s3_key,
                'created_at': timestamp,
                'ttl': ttl
            }
        )

        print(f"Created job {job_id} for JSON: {filename}")

        # Invoke Lambda asynchronously for processing
        lambda_client.invoke(
            FunctionName=os.environ['AWS_LAMBDA_FUNCTION_NAME'],
            InvocationType='Event',  # Async invocation
            Payload=json.dumps({
                'async_processing': True,
                'job_id': job_id,
                'filename': filename,
                'url': url,
                's3_key': s3_key
            })
        )

        print(f"Triggered async processing for job {job_id}")

        # Return job ID immediately
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({
                'status': 'In Progress...',
                'job_id': job_id,
                'message': 'JSON processing started'
            })
        }

    except Exception as e:
        print(f"Error creating job: {str(e)}")
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'})
        }


def process_json_job(event):
    """Background job to process JSON and store controls"""
    job_id = event['job_id']
    filename = event['filename']
    url = event['url']
    s3_key = event['s3_key']

    print(f"Starting async processing for job {job_id}")

    try:
        # Update job status to processing
        jobs_table.update_item(
            Key={'job_id': job_id},
            UpdateExpression='SET #status = :status, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'processing',
                ':updated': datetime.now().isoformat()
            }
        )

        # Fetch AWS Config Rules once for this job
        print("Fetching AWS Config Rules documentation...")
        config_rules_url = "https://docs.aws.amazon.com/config/latest/developerguide/managed-rules-by-aws-config.html"
        config_rules_key = f"config-rules/{job_id}/rules.html"

        try:
            import urllib3
            http = urllib3.PoolManager()
            response = http.request('GET', config_rules_url, timeout=30.0)

            if response.status != 200:
                print(f"Warning: Failed to fetch Config Rules (status {response.status}), continuing without them")
                config_rules_key = None
            else:
                config_rules_content = response.data

                # Store in S3
                s3_client.put_object(
                    Bucket=BUCKET_NAME,
                    Key=config_rules_key,
                    Body=config_rules_content,
                    ContentType='text/html'
                )
                print(f"Stored Config Rules at s3://{BUCKET_NAME}/{config_rules_key}")

        except Exception as e:
            print(f"Warning: Error fetching Config Rules: {str(e)}, continuing without them")
            config_rules_key = None

        # Download JSON from S3
        print(f"Downloading JSON from S3: {s3_key}")
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=s3_key)
        json_content = response['Body'].read().decode('utf-8')

        # Parse JSON
        print("Parsing JSON")
        data = json.loads(json_content)

        # Extract all controls recursively
        print("Extracting controls from JSON")
        controls = []
        extract_controls_recursive(data, controls)

        print(f"Found {len(controls)} controls in JSON")

        # Fan out control processing to separate Lambda invocations
        controls_dispatched = 0
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

            # Invoke control processor Lambda asynchronously
            try:
                payload = {
                    'control': control,
                    'job_id': job_id,
                    'source_file': filename,
                    's3_key': s3_key,
                    'url': url,
                    'timestamp': timestamp,
                    'config_rules_s3_key': config_rules_key
                }

                lambda_client.invoke(
                    FunctionName=CONTROL_PROCESSOR_FUNCTION_NAME,
                    InvocationType='Event',  # Async invocation
                    Payload=json.dumps(payload)
                )

                controls_dispatched += 1
                print(f"Dispatched control {control_id} for processing")

            except Exception as e:
                print(f"Error dispatching control {control_id}: {str(e)}")
                # Continue processing other controls

        print(f"Dispatched {controls_dispatched} controls for job {job_id}")

        # Update job status to completed
        jobs_table.update_item(
            Key={'job_id': job_id},
            UpdateExpression='SET #status = :status, controls_dispatched = :controls, completed_at = :completed',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'completed',
                ':controls': controls_dispatched,
                ':completed': datetime.now().isoformat()
            }
        )

        print(f"Job {job_id} completed successfully - dispatched {controls_dispatched} controls for processing")
        return {'statusCode': 200, 'body': json.dumps({'job_id': job_id, 'controls_dispatched': controls_dispatched})}

    except Exception as e:
        print(f"Error processing job {job_id}: {str(e)}")

        # Update job status to failed
        try:
            jobs_table.update_item(
                Key={'job_id': job_id},
                UpdateExpression='SET #status = :status, error_message = :error, failed_at = :failed',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':status': 'failed',
                    ':error': str(e),
                    ':failed': datetime.now().isoformat()
                }
            )
        except Exception as update_error:
            print(f"Failed to update job status: {str(update_error)}")

        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}


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
