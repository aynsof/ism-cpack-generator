from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    Duration,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_lambda as lambda_,
    aws_apigateway as apigateway,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_iam as iam,
    aws_dynamodb as dynamodb,
    aws_stepfunctions as sfn,
    aws_sns as sns,
)
from constructs import Construct
import json

class PdfUploadSystemStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 Bucket for JSON storage
        json_bucket = s3.Bucket(
            self, "JsonStorageBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.POST, s3.HttpMethods.PUT],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                    exposed_headers=["ETag", "x-amz-server-side-encryption", "x-amz-request-id", "x-amz-id-2"],
                    max_age=3000
                )
            ],
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        # DynamoDB table for storing ISM controls
        controls_table = dynamodb.Table(
            self, "ControlsTable",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY
        )

        # DynamoDB table for storing ISM control to Config Rule mappings
        config_mappings_table = dynamodb.Table(
            self, "ConfigMappingsTable",
            partition_key=dynamodb.Attribute(
                name="mapping_id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY
        )

        # Add GSI for querying by control_id
        config_mappings_table.add_global_secondary_index(
            index_name="ControlIdIndex",
            partition_key=dynamodb.Attribute(
                name="control_id",
                type=dynamodb.AttributeType.STRING
            )
        )

        # DynamoDB table for tracking job status
        jobs_table = dynamodb.Table(
            self, "JobsTable",
            partition_key=dynamodb.Attribute(
                name="job_id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl"  # Auto-delete old jobs after 24 hours
        )

        # Control processor Lambda - processes individual ISM controls
        control_processor_lambda = lambda_.Function(
            self, "ControlProcessorHandler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="control_processor.handler",
            code=lambda_.Code.from_asset("lambda"),
            environment={
                "CONTROLS_TABLE_NAME": controls_table.table_name,
                "CONFIG_MAPPINGS_TABLE_NAME": config_mappings_table.table_name,
                "BUCKET_NAME": json_bucket.bucket_name
            },
            timeout=Duration.seconds(60),
            memory_size=512
        )

        # Main orchestrator Lambda - handles /upload-url and /status endpoints only
        json_lambda = lambda_.Function(
            self, "JsonUploadHandler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.handler",
            code=lambda_.Code.from_asset("lambda"),
            environment={
                "BUCKET_NAME": json_bucket.bucket_name,
                "JOBS_TABLE_NAME": jobs_table.table_name
            },
            timeout=Duration.seconds(90),
            memory_size=512
        )

        # SNS Topic for email notifications
        notifications_topic = sns.Topic(
            self, "ProcessingNotifications",
            display_name="ISM Control Processing Notifications",
            topic_name="ism-control-processing-notifications"
        )

        # Create Job Lambda - creates job record in DynamoDB
        create_job_lambda = lambda_.Function(
            self, "CreateJobHandler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="create_job.handler",
            code=lambda_.Code.from_asset("lambda"),
            environment={
                "JOBS_TABLE_NAME": jobs_table.table_name
            },
            timeout=Duration.seconds(30),
            memory_size=256
        )

        # Process JSON Lambda - extracts controls from JSON
        process_json_lambda = lambda_.Function(
            self, "ProcessJsonHandler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="process_json.handler",
            code=lambda_.Code.from_asset("lambda"),
            environment={
                "BUCKET_NAME": json_bucket.bucket_name,
                "JOBS_TABLE_NAME": jobs_table.table_name
            },
            timeout=Duration.seconds(90),
            memory_size=512
        )

        # Send Notification Lambda - sends SNS email notifications
        send_notification_lambda = lambda_.Function(
            self, "SendNotificationHandler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="send_notification.handler",
            code=lambda_.Code.from_asset("lambda"),
            environment={
                "SNS_TOPIC_ARN": notifications_topic.topic_arn
            },
            timeout=Duration.seconds(30),
            memory_size=256
        )

        # Grant control processor Lambda permissions to DynamoDB
        controls_table.grant_write_data(control_processor_lambda)
        config_mappings_table.grant_write_data(control_processor_lambda)

        # Grant control processor Lambda permissions to S3
        json_bucket.grant_read(control_processor_lambda)

        # Grant control processor Lambda permissions to Bedrock
        control_processor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=['bedrock:InvokeModel'],
                resources=[
                    # Allow access to Opus 4.5 inference profile in this region
                    f'arn:aws:bedrock:ap-southeast-2:{self.account}:inference-profile/global.anthropic.claude-opus-4-5-20251101-v1:0',
                    # Allow access to foundation model in all regions (global inference profiles route across regions)
                    'arn:aws:bedrock:*::foundation-model/anthropic.claude-opus-4-5-20251101-v1:0'
                ]
            )
        )

        # Grant main Lambda permissions to S3
        json_bucket.grant_put(json_lambda)
        json_bucket.grant_read(json_lambda)

        # Grant main Lambda permissions to DynamoDB
        jobs_table.grant_read_data(json_lambda)  # Only read for status checks

        # Grant create_job Lambda permissions
        jobs_table.grant_read_write_data(create_job_lambda)

        # Grant process_json Lambda permissions
        json_bucket.grant_read(process_json_lambda)
        json_bucket.grant_write(process_json_lambda)  # For Config Rules storage
        jobs_table.grant_read_write_data(process_json_lambda)

        # Grant send_notification Lambda permissions
        notifications_topic.grant_publish(send_notification_lambda)
        send_notification_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=['sns:Subscribe', 'sns:ListSubscriptionsByTopic'],
                resources=[notifications_topic.topic_arn]
            )
        )

        # Step Functions State Machine
        # Load workflow definition from file
        with open('stepfunctions/workflow.asl.json', 'r') as f:
            workflow_definition = f.read()

        # Replace placeholders with actual ARNs
        workflow_definition = workflow_definition.replace(
            '${CreateJobLambdaArn}', create_job_lambda.function_arn
        ).replace(
            '${ProcessJsonLambdaArn}', process_json_lambda.function_arn
        ).replace(
            '${ControlProcessorLambdaArn}', control_processor_lambda.function_arn
        ).replace(
            '${SendNotificationLambdaArn}', send_notification_lambda.function_arn
        ).replace(
            '${BucketName}', json_bucket.bucket_name
        ).replace(
            '${JobsTableName}', jobs_table.table_name
        )

        # Create state machine
        control_processor_state_machine = sfn.StateMachine(
            self, "ControlProcessorStateMachine",
            state_machine_name="ISMControlProcessorWorkflow",
            definition_body=sfn.DefinitionBody.from_string(workflow_definition),
            timeout=Duration.minutes(30),
            tracing_enabled=True
        )

        # Grant state machine permissions to invoke Lambdas
        create_job_lambda.grant_invoke(control_processor_state_machine)
        process_json_lambda.grant_invoke(control_processor_state_machine)
        control_processor_lambda.grant_invoke(control_processor_state_machine)
        send_notification_lambda.grant_invoke(control_processor_state_machine)

        # Grant state machine permissions to update DynamoDB
        control_processor_state_machine.add_to_role_policy(
            iam.PolicyStatement(
                actions=['dynamodb:UpdateItem'],
                resources=[jobs_table.table_arn]
            )
        )

        # API Gateway
        api = apigateway.RestApi(
            self, "JsonUploadApi",
            rest_api_name="ISM JSON Upload API",
            description="API for ISM JSON control upload system",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=[
                    "Content-Type",
                    "X-Amz-Date",
                    "Authorization",
                    "X-Api-Key",
                    "X-Amz-Security-Token"
                ]
            )
        )

        # Lambda integration for /upload-url and /status endpoints
        lambda_integration = apigateway.LambdaIntegration(json_lambda)

        # API endpoints
        upload_url_resource = api.root.add_resource("upload-url")
        upload_url_resource.add_method("POST", lambda_integration)

        # Status endpoint with path parameter
        status_resource = api.root.add_resource("status")
        job_id_resource = status_resource.add_resource("{job_id}")
        job_id_resource.add_method("GET", lambda_integration)

        # Step Functions integration for /start-workflow endpoint
        # IAM role for API Gateway to start Step Functions
        api_sfn_role = iam.Role(
            self, "ApiStepFunctionsRole",
            assumed_by=iam.ServicePrincipal("apigateway.amazonaws.com")
        )
        control_processor_state_machine.grant_start_execution(api_sfn_role)

        # Step Functions integration
        sfn_integration = apigateway.AwsIntegration(
            service="states",
            action="StartExecution",
            integration_http_method="POST",
            options=apigateway.IntegrationOptions(
                credentials_role=api_sfn_role,
                passthrough_behavior=apigateway.PassthroughBehavior.NEVER,
                request_templates={
                    "application/json": json.dumps({
                        "input": "$util.escapeJavaScript($input.json('$'))",
                        "stateMachineArn": control_processor_state_machine.state_machine_arn
                    })
                },
                integration_responses=[
                    apigateway.IntegrationResponse(
                        status_code="200",
                        response_parameters={
                            "method.response.header.Access-Control-Allow-Origin": "'*'"
                        },
                        response_templates={
                            "application/json": """{
    "executionArn": $input.json('$.executionArn'),
    "startDate": $input.json('$.startDate')
}"""
                        }
                    ),
                    apigateway.IntegrationResponse(
                        status_code="500",
                        selection_pattern="5\\d{2}",
                        response_parameters={
                            "method.response.header.Access-Control-Allow-Origin": "'*'"
                        }
                    )
                ]
            )
        )

        # Add /start-workflow endpoint
        start_workflow_resource = api.root.add_resource("start-workflow")
        start_workflow_resource.add_method(
            "POST",
            sfn_integration,
            method_responses=[
                apigateway.MethodResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Origin": True
                    }
                ),
                apigateway.MethodResponse(
                    status_code="500",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Origin": True
                    }
                )
            ]
        )

        # S3 Bucket for frontend
        frontend_bucket = s3.Bucket(
            self, "FrontendBucket",
            public_read_access=False,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        # CloudFront Origin Access Identity
        oai = cloudfront.OriginAccessIdentity(
            self, "OAI",
            comment="OAI for PDF upload system frontend"
        )

        # Grant CloudFront read access to frontend bucket
        frontend_bucket.grant_read(oai)

        # CloudFront distribution
        distribution = cloudfront.Distribution(
            self, "FrontendDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(
                    frontend_bucket,
                    origin_access_identity=oai
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED
            ),
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0)
                )
            ]
        )

        # Deploy frontend files to S3
        s3deploy.BucketDeployment(
            self, "DeployFrontend",
            sources=[
                s3deploy.Source.asset("frontend"),
                s3deploy.Source.data(
                    "index.html",
                    self._inject_api_url("frontend/index.html", api.url)
                )
            ],
            destination_bucket=frontend_bucket,
            distribution=distribution,
            distribution_paths=["/*"]
        )

        # Outputs
        CfnOutput(self, "ApiUrl", value=api.url, description="API Gateway URL")
        CfnOutput(self, "CloudFrontUrl", value=f"https://{distribution.domain_name}", description="CloudFront URL")
        CfnOutput(self, "JsonBucketName", value=json_bucket.bucket_name, description="JSON Storage Bucket Name")
        CfnOutput(self, "ControlsTableName", value=controls_table.table_name, description="DynamoDB Controls Table Name")
        CfnOutput(self, "JobsTableName", value=jobs_table.table_name, description="DynamoDB Jobs Table Name")
        CfnOutput(self, "ControlProcessorFunctionName", value=control_processor_lambda.function_name, description="Control Processor Lambda Function Name")
        CfnOutput(self, "ConfigMappingsTableName", value=config_mappings_table.table_name, description="DynamoDB Config Mappings Table Name")
        CfnOutput(self, "StateMachineArn", value=control_processor_state_machine.state_machine_arn, description="Control Processor State Machine ARN")
        CfnOutput(self, "SNSTopicArn", value=notifications_topic.topic_arn, description="SNS Topic for Notifications")

    def _inject_api_url(self, file_path: str, api_url: str) -> str:
        """Read HTML file and inject API URL"""
        with open(file_path, 'r') as f:
            content = f.read()
        return content.replace('{{API_URL}}', api_url)
