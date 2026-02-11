import boto3
import os
import json

sns_client = boto3.client('sns')
SNS_TOPIC_ARN = os.environ['SNS_TOPIC_ARN']

def handler(event, context):
    """Send email notification via SNS"""

    email = event['email']
    subject = event['subject']
    message = event['message']

    print(f"Sending notification to {email}")
    print(f"Subject: {subject}")

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
