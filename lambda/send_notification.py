import boto3
import os
import json

sns_client = boto3.client('sns')
s3_client = boto3.client('s3')
SNS_TOPIC_ARN = os.environ['SNS_TOPIC_ARN']
OUTPUT_BUCKET_NAME = os.environ.get('OUTPUT_BUCKET_NAME', '')

def generate_presigned_urls(files, bucket_name, expiration=604800):
    """Generate presigned URLs for S3 objects (7 days default)"""
    presigned_urls = []

    for file_info in files:
        s3_key = file_info['s3_key']
        try:
            url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': s3_key},
                ExpiresIn=expiration
            )
            presigned_urls.append({
                'pack_name': file_info['pack_name'],
                'file_name': file_info['file_name'],
                'url': url,
                'rules_count': file_info['rules_count']
            })
        except Exception as e:
            print(f"Error generating presigned URL for {s3_key}: {e}")

    return presigned_urls

def handler(event, context):
    """Send email notification via SNS"""

    email = event['email']
    subject = event['subject']
    message = event['message']
    conformance_pack_result = event.get('conformancePackResult', {})
    bucket_name = event.get('bucket_name', OUTPUT_BUCKET_NAME)

    print(f"Sending notification to {email}")
    print(f"Subject: {subject}")

    # Check if conformance packs were generated
    if conformance_pack_result and 'files' in conformance_pack_result:
        files = conformance_pack_result['files']

        if files:
            print(f"Generating presigned URLs for {len(files)} conformance packs")
            presigned_urls = generate_presigned_urls(files, bucket_name)

            # Append conformance pack URLs to message
            message += "\n\n" + "="*60 + "\n"
            message += "AWS Config Conformance Packs Generated\n"
            message += "="*60 + "\n\n"

            for pack_info in presigned_urls:
                message += f"Pack: {pack_info['pack_name']}\n"
                message += f"Rules: {pack_info['rules_count']}\n"
                message += f"Download: {pack_info['url']}\n\n"

            message += f"Total Packs: {len(presigned_urls)}\n"
            message += f"URLs valid for 7 days\n"

    # Check if email is already subscribed
    subscriptions = sns_client.list_subscriptions_by_topic(TopicArn=SNS_TOPIC_ARN)

    existing = [s for s in subscriptions['Subscriptions']
                if s['Endpoint'] == email]

    if not existing:
        # Subscribe email (they'll get confirmation email on first use)
        print(f"Subscribing new email: {email}")
        sns_client.subscribe(
            TopicArn=SNS_TOPIC_ARN,
            Protocol='email',
            Endpoint=email
        )

        # First-time users need to confirm subscription
        message += "\n\nNote: This is your first notification. Please confirm your email subscription to receive future notifications."

    # Publish notification
    response = sns_client.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=subject,
        Message=message
    )

    print(f"Published notification, MessageId: {response['MessageId']}")

    return {
        'messageId': response['MessageId'],
        'email': email
    }
