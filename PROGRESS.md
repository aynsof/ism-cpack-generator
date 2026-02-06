# ISM Controls Upload System - Development Progress

## Project Overview
A serverless JSON upload and processing system built with AWS CDK. Users upload ISM catalog JSON files, the system recursively extracts all controls from the nested structure, and stores each control in DynamoDB using the control ID as the primary key and the prose statement as the value. Processing is asynchronous to avoid API Gateway timeout limits.

## Architecture

```
┌─────────────┐
│   Browser   │
└──────┬──────┘
       │
       v
┌─────────────────┐
│   CloudFront    │ (Static Frontend)
└─────────────────┘
       │
       v
┌─────────────────┐
│  API Gateway    │ (REST API)
└─────────────────┘
       │
       ├─── POST /upload-url        ──> Lambda (Generate presigned URL)
       ├─── POST /submit            ──> Lambda (Create job & trigger async processing)
       └─── GET /status/{job_id}    ──> Lambda (Get job status)
                                            │
                                            v
                                     ┌──────────────┐
                                     │ Lambda Async │ (Process JSON)
                                     └──────┬───────┘
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    v                                               v
             ┌──────────────┐                              ┌──────────────┐
             │  S3 Bucket   │ (JSON Storage)               │  DynamoDB    │
             └──────────────┘                              ├──────────────┤
                                                          │ Jobs Table    │
                                                          │ Controls Table│
                                                          └──────────────┘
```

## Components

### Infrastructure (CDK)
- **Frontend S3 Bucket**: Private bucket served via CloudFront with OAI
- **JSON Storage S3 Bucket**: Private, encrypted bucket with CORS for presigned uploads
- **CloudFront Distribution**: CDN with HTTPS redirect
- **API Gateway**: REST API with CORS enabled, 3 endpoints
- **Lambda Function**: Python 3.11, 90s timeout, 512MB memory
- **DynamoDB Tables**:
  - `JobsTable`: Tracks processing jobs with TTL (24h auto-delete)
  - `ControlsTable`: Stores ISM controls with id as primary key
- **IAM Roles**: Automatic permissions via CDK grants

### Frontend
- **Location**: `frontend/index.html` + `frontend/styles.css`
- **Features**:
  - JSON file upload (max 10MB)
  - URL input field (reserved for future use)
  - Real-time status polling (3-second intervals)
  - "In Progress..." and "Success!" messages
  - Loading states and error handling
  - Direct browser-to-S3 upload via presigned URLs

### Backend
- **Location**: `lambda/handler.py` (415 lines)
- **Runtime**: Python 3.11
- **Dependencies**: boto3 (no external dependencies needed)
- **Endpoints**:
  1. `POST /upload-url` - Generates presigned S3 POST URL
  2. `POST /submit` - Creates job and invokes async processing
  3. `GET /status/{job_id}` - Returns job status and results
- **Async Processing**:
  - Downloads JSON from S3
  - Parses JSON structure
  - Recursively extracts all controls from nested groups
  - Extracts id and prose statement from each control
  - Stores controls in DynamoDB with id as primary key
  - Updates job status (processing → completed/failed)

## Migration from PDF to JSON Processing (2026-02-06)

### Changes Made

**Infrastructure (CDK)**:
- Renamed `PdfStorageBucket` → `JsonStorageBucket`
- Renamed `PdfMatchesTable` → `ControlsTable`
- Renamed `PdfUploadHandler` → `JsonUploadHandler`
- Renamed `PdfUploadApi` → `JsonUploadApi`
- Updated environment variables: `MATCHES_TABLE_NAME` → `CONTROLS_TABLE_NAME`
- Changed accepted MIME type from `application/pdf` to `application/json`

**Lambda Handler**:
- Removed PyPDF2 dependency (no longer needed)
- Added `extract_controls_recursive()` function to recursively find all controls in nested JSON
- Changed `process_pdf_job()` → `process_json_job()`:
  - Downloads and parses JSON instead of PDF
  - Recursively traverses catalog structure to find all controls
  - Extracts control `id` and `prose` from statement parts
  - Stores each control with id as primary key
- Removed regex validation and matching logic
- Updated status responses to return `controls_stored` instead of `matches_found`

**Frontend**:
- Changed page title to "ISM Controls Upload"
- Updated file input to accept `.json` files
- Removed regex pattern input field (no longer needed)
- Kept URL field for future use
- Updated success message: "Stored X controls"

**Files Modified**:
- `pdf_upload_system/pdf_upload_system_stack.py` - Infrastructure changes
- `lambda/handler.py` - Complete rewrite for JSON processing
- `lambda/requirements.txt` - Removed PyPDF2
- `frontend/index.html` - UI updates for JSON upload

### Test Case: ISM PROTECTED Baseline
The system successfully processes the ISM catalog JSON file:
- **File**: `ISM_PROTECTED-baseline-resolved-profile_catalog.json` (2.1MB)
- **Controls Found**: 992 controls extracted from nested structure
- **Processing**: Handles deeply nested groups and arrays
- **Storage**: Each control stored with unique id (e.g., "ism-principle-gov-01")

## Issues Encountered & Solutions

### Issue 1: CloudFront 403 Forbidden
**Problem**: Frontend bucket had `website_index_document` parameter, causing CloudFront to use S3 website endpoint instead of REST API endpoint, which is incompatible with OAI.

**Solution**: Removed `website_index_document` parameter from frontend bucket configuration.

**File**: `pdf_upload_system/pdf_upload_system_stack.py:86`

### Issue 2: Double Slash in API URLs
**Problem**: API Gateway URL has trailing slash (`/prod/`), and frontend was adding another slash (`/upload-url`), creating `/prod//upload-url`.

**Solution**: Removed leading slash from frontend fetch URLs.

**Files**:
- `frontend/index.html:96` - Changed `${API_URL}/upload-url` to `${API_URL}upload-url`
- `frontend/index.html:137` - Changed `${API_URL}/submit` to `${API_URL}submit`

### Issue 3: S3 307 Temporary Redirect (CORS Failure)
**Problem**: Lambda was generating presigned URLs with global S3 endpoint (`s3.amazonaws.com`). S3 responded with 307 redirect to regional endpoint, causing CORS failures in browser.

**Solution**: Configured boto3 S3 client to use AWS Signature Version 4 and path-style addressing, forcing regional endpoint usage.

**File**: `lambda/handler.py:10-13`
```python
s3_config = Config(
    signature_version='s3v4',
    s3={'addressing_style': 'path'}
)
s3_client = boto3.client('s3', config=s3_config)
```

**Result**: Presigned URLs now use `s3.ap-southeast-2.amazonaws.com`

### Issue 4: S3 CORS Configuration
**Problem**: Initial CORS config didn't expose necessary headers for browser to read S3 responses.

**Solution**: Added exposed headers to S3 CORS configuration.

**File**: `pdf_upload_system/pdf_upload_system_stack.py:32`
```python
exposed_headers=["ETag", "x-amz-server-side-encryption", "x-amz-request-id", "x-amz-id-2"]
```

### Issue 5: API Gateway 29-Second Timeout
**Problem**: API Gateway has hard 29-second timeout limit. JSON processing could exceed this for large files.

**Solution**: Implemented asynchronous processing pattern:
- `/submit` creates job record and returns immediately with job ID
- Lambda invokes itself asynchronously for actual processing
- Frontend polls `/status/{job_id}` every 3 seconds
- Processing can now take up to 90 seconds (Lambda timeout)

**Files**:
- `pdf_upload_system/pdf_upload_system_stack.py:51-61` - Jobs table with TTL
- `lambda/handler.py:152-229` - Async job creation
- `lambda/handler.py:232-346` - Background JSON processing
- `lambda/handler.py:349-414` - Status endpoint
- `frontend/index.html:72-128` - Polling implementation

### Issue 6: DynamoDB Decimal Serialization
**Problem**: DynamoDB returns numbers as `Decimal` type, which can't be JSON serialized by default.

**Solution**: Added conversion function for Decimal to int/float before JSON serialization.

**File**: `lambda/handler.py:26-30`
```python
def decimal_to_number(obj):
    """Convert Decimal objects to int or float for JSON serialization"""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj
```

### Issue 7: Circular Dependency in CDK
**Problem**: Using `json_lambda.grant_invoke(json_lambda)` for self-invocation created circular dependency in CloudFormation.

**Solution**: Used `add_to_role_policy` with wildcard resource instead.

**File**: `pdf_upload_system/pdf_upload_system_stack.py:86-93`
```python
json_lambda.add_to_role_policy(
    iam.PolicyStatement(
        actions=['lambda:InvokeFunction'],
        resources=['*']
    )
)
```

## Deployment Details

### Stack Outputs (Current Deployment)
- **CloudFront URL**: https://d2noq38lnnxb2z.cloudfront.net
- **API Gateway URL**: https://5dqig1nkjh.execute-api.ap-southeast-2.amazonaws.com/prod/
- **JSON Bucket**: pdfuploadsystemstack-jsonstoragebucket62d4ac55-ti071jnsn2lc
- **Controls Table**: PdfUploadSystemStack-ControlsTable98BF324E-PCB7QOFGVR6Z
- **Jobs Table**: PdfUploadSystemStack-JobsTable1970BC16-51FUQ22Z9GBN
- **Region**: ap-southeast-2 (Sydney)

### Expected Processing Results
```json
{
  "job_id": "99b47b99-54b5-439c-a80a-9191ef05a387",
  "status": "completed",
  "filename": "ISM_PROTECTED-baseline-resolved-profile_catalog.json",
  "controls_stored": 992,
  "completed_at": "2026-02-06T06:12:15.123456",
  "message": "Success!"
}
```

### Verification Commands
```bash
# View uploaded files
aws s3 ls s3://pdfuploadsystemstack-jsonstoragebucket62d4ac55-ti071jnsn2lc/uploads/

# Check job status
curl https://5dqig1nkjh.execute-api.ap-southeast-2.amazonaws.com/prod/status/{job_id}

# View controls in DynamoDB
aws dynamodb scan --table-name PdfUploadSystemStack-ControlsTable98BF324E-PCB7QOFGVR6Z --region ap-southeast-2

# View specific control
aws dynamodb get-item --table-name PdfUploadSystemStack-ControlsTable98BF324E-PCB7QOFGVR6Z \
  --key '{"id":{"S":"ism-principle-gov-01"}}' --region ap-southeast-2

# View jobs in DynamoDB
aws dynamodb scan --table-name PdfUploadSystemStack-JobsTable1970BC16-51FUQ22Z9GBN --region ap-southeast-2

# Clear controls table
aws dynamodb scan --table-name PdfUploadSystemStack-ControlsTable98BF324E-PCB7QOFGVR6Z --region ap-southeast-2 \
  --attributes-to-get id --query 'Items[*].id.S' --output text | tr '\t' '\n' | \
  while read id; do aws dynamodb delete-item --table-name PdfUploadSystemStack-ControlsTable98BF324E-PCB7QOFGVR6Z \
  --key "{\"id\":{\"S\":\"$id\"}}" --region ap-southeast-2; done
```

## Key Technical Decisions

1. **Presigned URLs**: Direct browser-to-S3 uploads avoid Lambda payload limits (6MB) and reduce costs
2. **AWS Signature v4**: Required for regional endpoints and modern security standards
3. **Path-style addressing**: Ensures consistent regional endpoint usage
4. **CloudFront OAI**: Secure frontend hosting without public S3 bucket access
5. **Python 3.11**: Latest stable Lambda runtime with good performance
6. **Async Processing**: Avoids API Gateway 29s timeout, allows up to 90s processing time
7. **Status Polling**: Frontend polls every 3 seconds for user-friendly UX
8. **DynamoDB On-Demand**: Pay-per-request billing scales automatically
9. **TTL on Jobs**: Auto-cleanup after 24 hours prevents table bloat
10. **Recursive JSON Parsing**: Handles arbitrarily nested control structures in ISM catalogs
11. **Control ID as Primary Key**: Enables direct lookup and prevents duplicates

## File Structure

```
ism-cpack-generator/
├── app.py                                    # CDK app entry point
├── cdk.json                                  # CDK configuration
├── pdf_upload_system/
│   ├── __init__.py
│   └── pdf_upload_system_stack.py           # Infrastructure definition (~195 lines)
├── lambda/
│   ├── handler.py                           # Lambda function (415 lines)
│   ├── requirements.txt                     # boto3 only
│   └── [dependencies]/                      # Installed packages (boto3, etc)
├── frontend/
│   ├── index.html                           # Frontend UI with JavaScript (~235 lines)
│   └── styles.css                           # Modern responsive styling (161 lines)
├── test_json_upload.sh                      # JSON upload test script
├── requirements.txt                         # CDK dependencies
└── PROGRESS.md                              # This file

Total: ~1000 lines of code (excluding dependencies)
```

## DynamoDB Schema

### Jobs Table
```
Primary Key: job_id (String)
Attributes:
  - status: "processing" | "completed" | "failed"
  - filename: String
  - url: String (optional, reserved for future use)
  - s3_key: String
  - created_at: ISO timestamp
  - updated_at: ISO timestamp
  - completed_at: ISO timestamp (when completed)
  - failed_at: ISO timestamp (when failed)
  - controls_stored: Number (when completed)
  - error_message: String (when failed)
  - ttl: Number (Unix timestamp, auto-delete after 24h)
```

### Controls Table
```
Primary Key: id (String) - Control ID (e.g., "ism-principle-gov-01")
Attributes:
  - prose: String (the control statement text)
  - job_id: String (links to Jobs table)
  - source_file: String (original filename)
  - s3_key: String (full S3 path)
  - url: String (from form, optional)
  - timestamp: ISO timestamp
  - title: String (control title)
  - class: String (control class, e.g., "ISM-principle")

Note: Using control id as primary key means uploading the same catalog
will overwrite existing controls (idempotent operation)
```

## Deployment Commands

```bash
# Clean up old dependencies (if migrating from PDF version)
cd lambda
rm -rf PyPDF2 pypdf2-*.dist-info
cd ..

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

# Destroy stack (when done)
cdk destroy
```

## Success Criteria Met

✅ Frontend deployed on S3/CloudFront
✅ Form with JSON upload and URL input
✅ API Gateway with 3 endpoints (upload-url, submit, status)
✅ Async Lambda processing (no timeout issues)
✅ JSON parsing and recursive control extraction
✅ DynamoDB storage with control id as primary key
✅ Real-time status polling with "In Progress..." / "Success!" messages
✅ CORS properly configured on all endpoints
✅ Files stored in encrypted S3 bucket
✅ Successfully processes ISM catalog with 992 controls

## Known Limitations

1. **Lambda Timeout**: Max 90 seconds for JSON processing (very large files may timeout)
2. **Memory**: 512MB Lambda memory (adequate for JSON files up to ~10MB)
3. **File Size**: 10MB limit on frontend (S3 presigned URL has no hard limit)
4. **No Pagination**: Status endpoint doesn't return individual controls (summary only)
5. **No Authentication**: Public access (would add Cognito for production)
6. **TTL Cleanup**: Jobs auto-delete after 24h, controls persist forever
7. **Error Reporting**: Limited detail in frontend error messages
8. **Duplicate Handling**: Re-uploading same catalog overwrites existing controls (by design)
9. **No Validation**: Assumes JSON follows ISM catalog structure

## Future Enhancements

- [ ] Add endpoint to query controls by id
- [ ] Add pagination for viewing all controls
- [ ] Implement URL content fetching and processing
- [ ] Add authentication (Cognito)
- [ ] Server-side JSON schema validation
- [ ] CloudWatch metrics and alarms
- [ ] WebSocket for real-time updates (instead of polling)
- [ ] Batch processing for multiple JSON files
- [ ] Export controls to CSV/JSON
- [ ] Add GSI on job_id in controls table for efficient queries
- [ ] Add control versioning (track changes over time)
- [ ] Add control search functionality
- [ ] Support for other OSCAL catalog formats

## Development Timeline

- **Initial PDF system setup**: ~3 hours
- **Migration to JSON processing**: ~1 hour
  - Infrastructure changes: ~15 minutes
  - Lambda rewrite: ~25 minutes
  - Frontend updates: ~10 minutes
  - Deployment and testing: ~10 minutes

## Current Status

**✅ FULLY OPERATIONAL**

System successfully processes ISM catalog JSON files, recursively extracts controls, and stores them in DynamoDB with control id as primary key and prose statement as value. Ready for use with ISM PROTECTED baseline and other OSCAL catalog formats.

**Last Updated**: 2026-02-06
**Version**: 3.0 (ISM JSON Controls)
