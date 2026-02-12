import boto3
import os
import json

sns_client = boto3.client('sns')
ses_client = boto3.client('ses')
s3_client = boto3.client('s3')
SNS_TOPIC_ARN = os.environ['SNS_TOPIC_ARN']
OUTPUT_BUCKET_NAME = os.environ.get('OUTPUT_BUCKET_NAME', '')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'noreply@example.com')

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

def create_html_email(message, presigned_urls=None, html_report_url=None, is_new_subscriber=False):
    """Create styled HTML email"""

    # Build conformance packs section
    packs_html = ""
    if presigned_urls:
        packs_html = """
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 24px; border-radius: 8px; margin: 24px 0;">
            <h2 style="margin: 0 0 16px 0; font-size: 24px; font-weight: 600;">AWS Config Conformance Packs</h2>
            <p style="margin: 0 0 8px 0; opacity: 0.95;">Your conformance packs are ready for download</p>
        </div>

        <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 24px 0;">
        """

        for pack_info in presigned_urls:
            packs_html += f"""
            <div style="background: white; padding: 20px; margin-bottom: 16px; border-radius: 8px; border-left: 4px solid #667eea;">
                <h3 style="margin: 0 0 12px 0; font-size: 18px; color: #1a202c;">{pack_info['pack_name']}</h3>
                <p style="margin: 0 0 12px 0; color: #4a5568;">
                    <span style="display: inline-block; background: #e6f3ff; color: #0066cc; padding: 4px 12px; border-radius: 4px; font-size: 14px; font-weight: 500;">
                        {pack_info['rules_count']} Rules
                    </span>
                </p>
                <a href="{pack_info['url']}" style="display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 500; transition: transform 0.2s;">
                    Download YAML
                </a>
            </div>
            """

        packs_html += f"""
        </div>

        <div style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 16px; border-radius: 4px; margin: 24px 0;">
            <p style="margin: 0; color: #856404;">
                <strong>📦 Total Packs:</strong> {len(presigned_urls)} &nbsp;&nbsp;|&nbsp;&nbsp; <strong>⏰ Valid for:</strong> 7 days
            </p>
        </div>
        """

    # Build HTML report section
    html_report_html = ""
    if html_report_url:
        html_report_html = """
        <div style="background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); color: white; padding: 24px; border-radius: 8px; margin: 32px 0 24px 0;">
            <h2 style="margin: 0 0 16px 0; font-size: 24px; font-weight: 600;">ISM Control Mappings Report</h2>
            <p style="margin: 0 0 8px 0; opacity: 0.95;">Interactive report with search and filtering</p>
        </div>

        <div style="background: white; padding: 24px; border-radius: 8px; border: 2px solid #11998e; margin: 24px 0;">
            <p style="margin: 0 0 16px 0; color: #2d3748; font-size: 15px;">
                View an interactive HTML report showing all ISM controls mapped to AWS Config Rules with AI-generated explanations.
            </p>

            <div style="margin: 20px 0;">
                <h4 style="margin: 0 0 12px 0; color: #1a202c; font-size: 16px;">Report Features:</h4>
                <ul style="margin: 0; padding-left: 20px; color: #4a5568;">
                    <li style="margin-bottom: 8px;">Complete control-to-rule mappings</li>
                    <li style="margin-bottom: 8px;">AI-generated relevance explanations</li>
                    <li style="margin-bottom: 8px;">Real-time search and filtering</li>
                    <li style="margin-bottom: 8px;">Statistics dashboard</li>
                </ul>
            </div>

            <a href="{}" style="display: inline-block; background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); color: white; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: 500; font-size: 16px; margin-top: 12px;">
                View Interactive Report
            </a>
        </div>
        """.format(html_report_url)

    # Build subscriber note
    subscriber_note = ""
    if is_new_subscriber:
        subscriber_note = """
        <div style="background: #e3f2fd; border-left: 4px solid #2196f3; padding: 16px; border-radius: 4px; margin: 24px 0;">
            <p style="margin: 0; color: #1565c0;">
                <strong>📧 First-time notification:</strong> Please confirm your email subscription to receive future notifications.
            </p>
        </div>
        """

    html_body = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ISM Control Processing Complete</title>
    </head>
    <body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #f0f2f5;">
        <div style="max-width: 680px; margin: 0 auto; padding: 40px 20px;">
            <!-- Header -->
            <div style="background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); color: white; padding: 32px; border-radius: 12px 12px 0 0; text-align: center;">
                <h1 style="margin: 0 0 8px 0; font-size: 28px; font-weight: 600;">Processing Complete</h1>
                <p style="margin: 0; opacity: 0.9; font-size: 16px;">ISM Control Upload System</p>
            </div>

            <!-- Main Content -->
            <div style="background: white; padding: 32px; border-radius: 0 0 12px 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
                <!-- Status Message -->
                <div style="margin-bottom: 24px;">
                    <p style="margin: 0; color: #2d3748; font-size: 16px; line-height: 1.6;">
                        {message}
                    </p>
                </div>

                {packs_html}

                {html_report_html}

                {subscriber_note}

                <!-- Footer -->
                <div style="margin-top: 32px; padding-top: 24px; border-top: 1px solid #e2e8f0; text-align: center;">
                    <p style="margin: 0 0 8px 0; color: #718096; font-size: 14px;">
                        Generated by ISM Control Upload System
                    </p>
                    <p style="margin: 0; color: #a0aec0; font-size: 13px;">
                        Powered by AWS and Claude AI
                    </p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    return html_body


def handler(event, context):
    """Send email notification via SNS"""

    email = event['email']
    subject = event['subject']
    message = event['message']
    conformance_pack_result = event.get('conformancePackResult', {})
    bucket_name = event.get('bucket_name', OUTPUT_BUCKET_NAME)

    print(f"Sending notification to {email}")
    print(f"Subject: {subject}")

    presigned_urls = None
    html_report_url = None

    # Check if conformance packs were generated
    if conformance_pack_result and 'files' in conformance_pack_result:
        files = conformance_pack_result['files']

        if files:
            print(f"Generating presigned URLs for {len(files)} conformance packs")
            presigned_urls = generate_presigned_urls(files, bucket_name)

    # Check if HTML mappings report was generated
    if conformance_pack_result and 'html_mappings_report' in conformance_pack_result:
        html_report = conformance_pack_result['html_mappings_report']
        s3_key = html_report.get('s3_key')

        if s3_key:
            print(f"Generating presigned URL for HTML mappings report")
            try:
                html_report_url = s3_client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': bucket_name, 'Key': s3_key},
                    ExpiresIn=604800  # 7 days
                )
            except Exception as e:
                print(f"Error generating presigned URL for HTML report: {e}")

    # Check if email is already subscribed
    subscriptions = sns_client.list_subscriptions_by_topic(TopicArn=SNS_TOPIC_ARN)

    existing = [s for s in subscriptions['Subscriptions']
                if s['Endpoint'] == email]

    is_new_subscriber = False
    if not existing:
        # Subscribe email (they'll get confirmation email on first use)
        print(f"Subscribing new email: {email}")
        sns_client.subscribe(
            TopicArn=SNS_TOPIC_ARN,
            Protocol='email',
            Endpoint=email
        )
        is_new_subscriber = True

    # Create HTML email
    html_body = create_html_email(message, presigned_urls, html_report_url, is_new_subscriber)

    # Create plain text fallback
    text_body = message
    if presigned_urls:
        text_body += "\n\n" + "="*60 + "\n"
        text_body += "AWS Config Conformance Packs Generated\n"
        text_body += "="*60 + "\n\n"
        for pack_info in presigned_urls:
            text_body += f"Pack: {pack_info['pack_name']}\n"
            text_body += f"Rules: {pack_info['rules_count']}\n"
            text_body += f"Download: {pack_info['url']}\n\n"
        text_body += f"Total Packs: {len(presigned_urls)}\n"
        text_body += f"URLs valid for 7 days\n"

    if html_report_url:
        text_body += "\n\n" + "="*60 + "\n"
        text_body += "ISM Control Mappings Report\n"
        text_body += "="*60 + "\n\n"
        text_body += "View an interactive HTML report showing all ISM controls\n"
        text_body += "mapped to AWS Config Rules with explanations:\n\n"
        text_body += f"{html_report_url}\n\n"
        text_body += "This report includes:\n"
        text_body += "- Complete control-to-rule mappings\n"
        text_body += "- Relevance explanations for each mapping\n"
        text_body += "- Interactive search and filtering\n"
        text_body += "- URL valid for 7 days\n"

    if is_new_subscriber:
        text_body += "\n\nNote: This is your first notification. Please confirm your email subscription to receive future notifications."

    # Send HTML email using SES
    try:
        response = ses_client.send_email(
            Source=SENDER_EMAIL,
            Destination={
                'ToAddresses': [email]
            },
            Message={
                'Subject': {
                    'Data': subject,
                    'Charset': 'UTF-8'
                },
                'Body': {
                    'Text': {
                        'Data': text_body,
                        'Charset': 'UTF-8'
                    },
                    'Html': {
                        'Data': html_body,
                        'Charset': 'UTF-8'
                    }
                }
            }
        )

        print(f"Sent HTML email via SES, MessageId: {response['MessageId']}")

        return {
            'messageId': response['MessageId'],
            'email': email
        }

    except Exception as e:
        print(f"Error sending email via SES: {e}")

        # Fallback to SNS if SES fails
        print("Falling back to SNS for plain text notification")

        message_payload = {
            'default': text_body,
            'email': text_body
        }

        response = sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=json.dumps(message_payload),
            MessageStructure='json'
        )

        print(f"Published notification via SNS, MessageId: {response['MessageId']}")

        return {
            'messageId': response['MessageId'],
            'email': email,
            'fallback': 'sns'
        }
