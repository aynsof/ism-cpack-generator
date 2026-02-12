# ISM Controls Upload System - Quick Reference

## Current Status (Version 7.3)
**✅ FULLY OPERATIONAL** - Automated ISM control processing with conformance pack generation, HTML mappings report, styled email notifications, and real-time progress tracking

**Last Updated**: 2026-02-12

## System Overview
Serverless system that processes ISM catalog JSON files:
1. Upload JSON via web UI
2. Extract controls using Step Functions orchestration
3. Map controls to AWS Config Rules using Bedrock (Claude Opus 4.5)
4. Generate AWS Config Conformance Pack YAML files
5. Generate interactive HTML control mappings report
6. Email presigned download URLs (7-day validity)
7. Real-time progress tracking with animated progress bar (10% → 100%)

**Performance**: ~60-90 seconds for 992 controls
**Cost**: ~$17-25 per upload ($15-20 control processing + $2-5 pack generation)

## Architecture

**Frontend**: https://d2noq38lnnxb2z.cloudfront.net (CloudFront + S3)
- Default URL: AWS Config managed rules documentation
- Consistent styling across all input fields
- Real-time progress bar with status updates

**Step Functions Workflow** (16 states with progress tracking):
```
1. CreateJob → 2. UpdateProgressProcessingJSON (10%)
→ 3. ProcessJSON → 4. UpdateProgressProcessingControls (20%)
→ 5. ProcessControls (Map, concurrency=100) → 6. UpdateJobCompleted (50%)
→ 7. InitializeConformancePacks → 8. UpdateProgressProcessingBatches (60%)
→ 9. ProcessConformancePackBatches (Map, concurrency=10)
→ 10. UpdateProgressAggregating (80%) → 11. AggregateConformancePacks
→ 12. UpdateProgressSendingNotification (90%) → 13. SendSuccessNotification
→ 14. MarkJobCompleted (100%)
```

**API Endpoints**:
- POST `/upload-url` - Generate presigned S3 URL
- POST `/start-workflow` - Trigger Step Functions workflow
- GET `/status/{job_id}` - Get job status with progress (current_step, progress_percentage, total_controls)

**Lambda Functions**:
- JsonUploadHandler: Generate presigned URLs, status queries
- CreateJobHandler: Create DynamoDB job records
- ProcessJsonHandler: Extract controls, fetch Config Rules
- ControlProcessorHandler: Process individual controls (60s, 512MB) + Bedrock mapping
- ConformancePackInitializerHandler: Initialize pack generation (60s, 512MB)
- ConformancePackBatchProcessorHandler: Process Config Rules batches (300s, 1024MB)
- ConformancePackAggregatorHandler: Generate YAML files + HTML mappings report (120s, 512MB)
- SendNotificationHandler: SES styled HTML emails with presigned URLs for YAML + HTML (30s, 256MB) - Falls back to SNS if SES fails

**DynamoDB Tables**:
- JobsTable: Track processing jobs (TTL 24h)
- ControlsTable: Store ISM controls (id as primary key)
- ConfigMappingsTable: Store control→Config Rule mappings (GSI on control_id)

**Other Services**:
- SES: Styled HTML email notifications (sender: `noreply@kingsjam147655097661.email.connect.aws`)
- SNS: Fallback email notifications (topic: `ism-control-processing-notifications`)
- S3: JSON storage, Config Rules docs, YAML outputs
- Bedrock: Claude Opus 4.5 via `global.anthropic.claude-opus-4-5-20251101-v1:0`

## Current Deployment

**Region**: ap-southeast-2 (Sydney)

**URLs**:
- CloudFront: https://d2noq38lnnxb2z.cloudfront.net
- API Gateway: https://5dqig1nkjh.execute-api.ap-southeast-2.amazonaws.com/prod/

**Resources**:
- JSON Bucket: `pdfuploadsystemstack-jsonstoragebucket62d4ac55-ti071jnsn2lc`
- Controls Table: `PdfUploadSystemStack-ControlsTable98BF324E-PCB7QOFGVR6Z`
- Jobs Table: `PdfUploadSystemStack-JobsTable1970BC16-51FUQ22Z9GBN`
- Mappings Table: `PdfUploadSystemStack-ConfigMappingsTableECB154B2-6LBWYSLUDXOE`
- CloudFront Distribution: `E3MCIBPC6972X7`

## DynamoDB Schema

**Jobs Table**:
```
Primary Key: job_id (String)
Attributes: status, filename, s3_key, email, execution_arn, created_at,
            completed_at, controls_dispatched, error_message, ttl (24h),
            current_step, progress_percentage, total_controls
```

**Controls Table**:
```
Primary Key: id (String) - e.g., "ism-principle-gov-01"
Attributes: prose, job_id, source_file, s3_key, timestamp, title, class
Note: Uploading same catalog overwrites existing controls (idempotent)
```

**ConfigMappings Table**:
```
Primary Key: mapping_id (String) - UUID
GSI: ControlIdIndex on control_id
Attributes: control_id, control_description, config_rule_identifier,
            relevance_explanation, job_id, timestamp, bedrock_model
```

## File Structure

```
ism-cpack-generator/
├── app.py                                    # CDK entry point
├── cdk.json                                  # CDK config
├── pdf_upload_system/
│   └── pdf_upload_system_stack.py           # Infrastructure (420 lines)
├── lambda/
│   ├── handler.py                           # Main orchestrator
│   ├── create_job.py                        # Job creation
│   ├── process_json.py                      # JSON parsing
│   ├── control_processor.py                 # Control processing + Bedrock
│   ├── conformance_pack_initializer.py      # Pack init (228 lines)
│   ├── conformance_pack_batch_processor.py  # Batch processing (273 lines)
│   ├── conformance_pack_aggregator.py       # YAML + HTML generation (590 lines)
│   └── send_notification.py                 # SNS notifications (120 lines)
├── stepfunctions/
│   └── workflow.asl.json                    # Step Functions definition (185 lines)
├── frontend/
│   ├── index.html                           # UI (235 lines)
│   └── styles.css                           # Styling (161 lines)
├── generate_conformance_packs.py            # Standalone offline generator (605 lines)
├── test_6_controls.json                     # Small test file
├── test_json_upload.sh                      # Test script
├── clear_controls_table.sh                  # Maintenance script
└── PROGRESS.md                              # This file
```

## Deployment Commands

```bash
# Install/update dependencies
cd lambda
pip install -r requirements.txt -t . --upgrade
cd ..

# Deploy infrastructure
source .venv/bin/activate
pip install -r requirements.txt
cdk deploy --require-approval never

# Invalidate CloudFront cache (after frontend changes)
aws cloudfront create-invalidation --distribution-id E3MCIBPC6972X7 --paths "/*"

# Destroy stack
cdk destroy
```

## Verification Commands

```bash
# View uploaded files
aws s3 ls s3://pdfuploadsystemstack-jsonstoragebucket62d4ac55-ti071jnsn2lc/uploads/

# Check job status
curl https://5dqig1nkjh.execute-api.ap-southeast-2.amazonaws.com/prod/status/{job_id}

# View control
aws dynamodb get-item --table-name PdfUploadSystemStack-ControlsTable98BF324E-PCB7QOFGVR6Z \
  --key '{"id":{"S":"ism-principle-gov-01"}}' --region ap-southeast-2

# Query mappings for control
aws dynamodb query \
  --table-name PdfUploadSystemStack-ConfigMappingsTableECB154B2-6LBWYSLUDXOE \
  --index-name ControlIdIndex \
  --key-condition-expression "control_id = :cid" \
  --expression-attribute-values '{":cid":{"S":"ism-1984"}}' \
  --region ap-southeast-2

# Clear controls table
./clear_controls_table.sh

# View Step Functions executions
aws stepfunctions list-executions --state-machine-arn {state_machine_arn} --region ap-southeast-2
```

## Testing Workflow

```bash
# 1. Clear tables
./clear_controls_table.sh

# 2. Upload test file
# Use test_6_controls.json (6 controls) for quick testing
# Or ISM_PROTECTED-baseline-resolved-profile_catalog.json (992 controls) for full test

# 3. Via frontend
# Navigate to https://d2noq38lnnxb2z.cloudfront.net
# Upload JSON, enter email, submit

# 4. Via test script
./test_json_upload.sh

# 5. Check email for completion notification with:
#    - Presigned YAML conformance pack URLs
#    - Interactive HTML control mappings report URL
```

## S3 Output Structure

```
s3://bucket-name/
├── uploads/{timestamp}/{filename}.json
├── config-rules/{job_id}/rules.html
└── conformance-packs/{job_id}/
    ├── docs/config-rules-documentation.html
    ├── batch-results/batch-001.json
    ├── conformance-pack-ism-controls-01.yaml
    ├── control-mappings-report.html
    └── GENERATION_REPORT.md
```

## Key Technical Notes

**Bedrock Configuration**:
- Model: Claude Opus 4.5 (`global.anthropic.claude-opus-4-5-20251101-v1:0`)
- Temperature: 0.0 (deterministic)
- Max tokens: 4096
- Cost: ~$15 per million input tokens

**S3 Presigned URLs**:
- Use AWS Signature v4 and path-style addressing
- Config in `lambda/handler.py:10-13`:
```python
s3_config = Config(signature_version='s3v4', s3={'addressing_style': 'path'})
s3_client = boto3.client('s3', config=s3_config)
```

**Config Rules Distribution**:
- Main Lambda fetches once, stores in S3
- Control processors read from S3 (992 concurrent GETs)
- ~190KB HTML document per job

**Fan-out Architecture**:
- Main Lambda extracts controls → dispatches to Control Processors
- Parallel processing: 100 control processors, 10 pack batch processors
- Each control processed independently (isolation)

**Error Handling**:
- Step Functions Catch blocks for all conformance pack states
- Graceful degradation: control processing succeeds even if pack gen fails
- Email sent regardless of pack generation status

**Important IAM Note**:
- Bedrock inference profile requires wildcard region in IAM policy
- `arn:aws:bedrock:*::foundation-model/...` (not region-specific)

**Race Condition Handling**:
- Frontend generates job_id client-side using `crypto.randomUUID()`
- Treats 404 as "job not created yet" during polling
- Continues polling instead of throwing error

**HTML Mappings Report**:
- Interactive web report showing all control-to-rule mappings
- Generated by ConformancePackAggregatorHandler after YAML generation
- Queries ConfigMappingsTable filtered by job_id
- Features:
  - Modern responsive design with gradient header
  - Statistics dashboard (total mappings, unique controls, config rules)
  - Real-time client-side search and filtering
  - Displays: Control ID, control description, Config Rule, relevance explanation
  - Print-friendly styling
  - Mobile responsive
- Uploaded to S3 at `conformance-packs/{job_id}/control-mappings-report.html`
- Presigned URL (7-day validity) included in email notification

**Styled HTML Email Notifications**:
- Professional HTML emails sent via Amazon SES (with plain text fallback)
- Features:
  - Gradient header (blue) with "Processing Complete" title
  - Conformance packs section (purple gradient) with styled download buttons
  - HTML mappings report section (green gradient) with feature list
  - Color-coded info boxes (yellow for validity, blue for new subscribers)
  - Clean typography with system fonts
  - Responsive design (mobile and desktop)
  - Plain text fallback for older email clients
- Falls back to SNS for plain text if SES fails
- Sender: `noreply@kingsjam147655097661.email.connect.aws`
- Requires verified sender email/domain in SES
- SES production access enabled (can send to any email)

**Real-time Progress Tracking**:
- Animated progress bar in frontend UI showing 0-100% completion
- Step Functions updates DynamoDB with progress at each major stage:
  - 10%: Processing JSON file
  - 20%: Processing controls with AI
  - 50%: Initializing conformance packs
  - 60%: Processing conformance pack batches
  - 80%: Generating YAML files and HTML report
  - 90%: Sending notification
  - 100%: Completed
- Frontend polls status endpoint every 3 seconds (6-minute timeout)
- Displays current step description and percentage
- Progress data stored in Jobs table: `current_step`, `progress_percentage`, `total_controls`
- Uses native Step Functions DynamoDB integration (no additional Lambdas)

## Known Limitations

1. **Lambda Concurrency**: Default 1000 concurrent executions (can request increase)
2. **Step Functions Timeout**: 30 minutes (sufficient for current workloads)
3. **File Size**: 10MB frontend limit
4. **No Authentication**: Public access (add Cognito for production)
5. **No Retry Logic**: Failed processors not automatically retried (consider DLQ)
6. **SES Sender Verification**: Requires verified sender email/domain in SES (currently using verified domain)

## Standalone Conformance Pack Generator

Offline tool for generating packs from existing DynamoDB mappings:

```bash
# Install dependencies
pip install -r conformance-pack-requirements.txt

# Generate packs
python3 generate_conformance_packs.py --controls-table control-config-rules-mappings

# Deploy pack
./test_deploy.sh output/conformance-pack-ism-controls.yaml
```

Benefits: Reproducibility, filtering, offline capability, version control

## Version History

- **v1.0-5.0**: Initial PDF system → JSON processing → fan-out architecture → Bedrock integration
- **v6.0** (2026-02-11): Migrated to Step Functions orchestration, added SNS notifications
- **v6.1** (2026-02-11): Recovered standalone conformance pack generator
- **v7.0** (2026-02-12): Integrated conformance pack generation into Step Functions workflow
- **v7.1** (2026-02-12): Added interactive HTML control mappings report with search functionality
- **v7.2** (2026-02-12): Migrated to SES for styled HTML email notifications with gradient headers, professional buttons, and responsive design
- **v7.3** (2026-02-12): **Current** - Added real-time progress tracking with animated progress bar showing 6 workflow stages (10% → 100%)

## CloudWatch Observability

**Lambda Logs**:
- `/aws/lambda/PdfUploadSystemStack-JsonUploadHandler-*`
- `/aws/lambda/PdfUploadSystemStack-CreateJobHandler-*`
- `/aws/lambda/PdfUploadSystemStack-ProcessJsonHandler-*`
- `/aws/lambda/PdfUploadSystemStack-ControlProcessorHandler-*`
- `/aws/lambda/PdfUploadSystemStack-ConformancePackInitializerHan-*`
- `/aws/lambda/PdfUploadSystemStack-ConformancePackBatchProcessor-*`
- `/aws/lambda/PdfUploadSystemStack-ConformancePackAggregatorHand-*`
- `/aws/lambda/PdfUploadSystemStack-SendNotificationHandler-*`

**Step Functions**:
- View executions in AWS Console → Step Functions → ISMControlProcessorWorkflow
- X-Ray tracing enabled for complete workflow visualization

**Typical Log Messages**:
- Main Lambda: "Fetching AWS Config Rules" → "Stored Config Rules at s3://..."
- Control Processor: "Querying Bedrock for control {id}" → "Stored N Config Rule mappings"
- Initializer: "Found N unique Config Rules across M controls"
- Batch Processor: "Processing batch {id} with {count} rules"
- Aggregator: "Generated {count} conformance pack files" → "Generating HTML control mappings report..." → "✓ Found N control mappings"
- Notification Handler: "Sent HTML email via SES, MessageId: {id}" (or "Falling back to SNS" if SES fails)

## Future Enhancements

- [ ] Add authentication (Cognito)
- [ ] API endpoint to query controls/mappings
- [ ] Web dashboard for pack management
- [ ] CloudWatch metrics and alarms
- [ ] DLQ for failed processors
- [ ] Completion tracking for all processors
- [ ] Pack validation before upload
- [ ] Multi-account conformance pack deployment
- [ ] WebSocket for real-time updates (vs polling)
- [ ] Support for custom Config Rules
