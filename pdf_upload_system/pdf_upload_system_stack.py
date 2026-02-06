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
)
from constructs import Construct

class PdfUploadSystemStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 Bucket for PDF storage
        pdf_bucket = s3.Bucket(
            self, "PdfStorageBucket",
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

        # DynamoDB table for storing regex matches
        matches_table = dynamodb.Table(
            self, "PdfMatchesTable",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY
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

        # Lambda function
        pdf_lambda = lambda_.Function(
            self, "PdfUploadHandler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.handler",
            code=lambda_.Code.from_asset("lambda"),
            environment={
                "BUCKET_NAME": pdf_bucket.bucket_name,
                "MATCHES_TABLE_NAME": matches_table.table_name,
                "JOBS_TABLE_NAME": jobs_table.table_name
            },
            timeout=Duration.seconds(90),
            memory_size=512
        )

        # Grant Lambda permissions to S3
        pdf_bucket.grant_put(pdf_lambda)
        pdf_bucket.grant_read(pdf_lambda)

        # Grant Lambda permissions to DynamoDB
        matches_table.grant_read_write_data(pdf_lambda)
        jobs_table.grant_read_write_data(pdf_lambda)

        # Grant Lambda permission to invoke itself asynchronously
        # Using wildcard to avoid circular dependency
        pdf_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=['lambda:InvokeFunction'],
                resources=['*']  # Or restrict to same account/region if needed
            )
        )

        # API Gateway
        api = apigateway.RestApi(
            self, "PdfUploadApi",
            rest_api_name="PDF Upload API",
            description="API for PDF upload system",
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

        # Lambda integration
        lambda_integration = apigateway.LambdaIntegration(pdf_lambda)

        # API endpoints
        upload_url_resource = api.root.add_resource("upload-url")
        upload_url_resource.add_method("POST", lambda_integration)

        submit_resource = api.root.add_resource("submit")
        submit_resource.add_method("POST", lambda_integration)

        # Status endpoint with path parameter
        status_resource = api.root.add_resource("status")
        job_id_resource = status_resource.add_resource("{job_id}")
        job_id_resource.add_method("GET", lambda_integration)

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
        CfnOutput(self, "PdfBucketName", value=pdf_bucket.bucket_name, description="PDF Storage Bucket Name")
        CfnOutput(self, "MatchesTableName", value=matches_table.table_name, description="DynamoDB Matches Table Name")
        CfnOutput(self, "JobsTableName", value=jobs_table.table_name, description="DynamoDB Jobs Table Name")

    def _inject_api_url(self, file_path: str, api_url: str) -> str:
        """Read HTML file and inject API URL"""
        with open(file_path, 'r') as f:
            content = f.read()
        return content.replace('{{API_URL}}', api_url)
