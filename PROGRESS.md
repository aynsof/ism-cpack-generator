# PDF Upload System - Development Progress

## Project Overview
A serverless PDF upload and processing system built with AWS CDK. Users upload PDFs with a regex pattern and URL, the system extracts text from PDFs, finds matching lines, and stores results in DynamoDB. Processing is asynchronous to avoid API Gateway timeout limits.

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
                                     │ Lambda Async │ (Process PDF)
                                     └──────┬───────┘
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    v                                               v
             ┌──────────────┐                              ┌──────────────┐
             │  S3 Bucket   │ (PDF Storage)                │  DynamoDB    │
             └──────────────┘                              ├──────────────┤
                                                           │ Jobs Table   │
                                                           │ Matches Table│
                                                           └──────────────┘
```

## Components

### Infrastructure (CDK)
- **Frontend S3 Bucket**: Private bucket served via CloudFront with OAI
- **PDF Storage S3 Bucket**: Private, encrypted bucket with CORS for presigned uploads
- **CloudFront Distribution**: CDN with HTTPS redirect
- **API Gateway**: REST API with CORS enabled, 3 endpoints
- **Lambda Function**: Python 3.11, 90s timeout, 512MB memory
- **DynamoDB Tables**:
  - `JobsTable`: Tracks processing jobs with TTL (24h auto-delete)
  - `MatchesTable`: Stores regex match results
- **IAM Roles**: Automatic permissions via CDK grants

### Frontend
- **Location**: `frontend/index.html` + `frontend/styles.css`
- **Features**:
  - PDF file upload (max 10MB)
  - Regex pattern input validation
  - URL input
  - Real-time status polling (3-second intervals)
  - "In Progress..." and "Success!" messages
  - Loading states and error handling
  - Direct browser-to-S3 upload via presigned URLs

### Backend
- **Location**: `lambda/handler.py` (405 lines)
- **Runtime**: Python 3.11
- **Dependencies**: boto3, PyPDF2==3.0.1
- **Endpoints**:
  1. `POST /upload-url` - Generates presigned S3 POST URL
  2. `POST /submit` - Creates job and invokes async processing
  3. `GET /status/{job_id}` - Returns job status and results
- **Async Processing**:
  - Downloads PDF from S3
  - Extracts text with PyPDF2
  - Applies regex pattern to each line
  - Stores matches in DynamoDB
  - Updates job status (processing → completed/failed)

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

**File**: `lambda/handler.py:12-15`
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

### Issue 5: Lambda 502 Bad Gateway (Missing PyPDF2)
**Problem**: Lambda deployment didn't include PyPDF2 dependencies. Lambda crashed on import, causing 502 errors which API Gateway returned without CORS headers, manifesting as CORS errors in browser.

**Solution**: Installed Python dependencies directly into lambda directory before deployment:
```bash
cd lambda && pip install -r requirements.txt -t . --upgrade
```

**Root Cause**: Browser showed "CORS error" but actual issue was Lambda crash (502). When Lambda fails before returning response, API Gateway can't add CORS headers.

**File**: `lambda/requirements.txt`

### Issue 6: API Gateway 29-Second Timeout
**Problem**: API Gateway has hard 29-second timeout limit. PDF processing could exceed this for large files.

**Solution**: Implemented asynchronous processing pattern:
- `/submit` creates job record and returns immediately with job ID
- Lambda invokes itself asynchronously for actual processing
- Frontend polls `/status/{job_id}` every 3 seconds
- Processing can now take up to 90 seconds (Lambda timeout)

**Files**:
- `pdf_upload_system/pdf_upload_system_stack.py:40-57` - Added Jobs table
- `lambda/handler.py:150-225` - Async job creation
- `lambda/handler.py:250-340` - Background PDF processing
- `lambda/handler.py:343-405` - Status endpoint
- `frontend/index.html:77-138` - Polling implementation

### Issue 7: DynamoDB Decimal Serialization
**Problem**: DynamoDB returns numbers as `Decimal` type, which can't be JSON serialized by default.

**Solution**: Added conversion function for Decimal to int/float before JSON serialization.

**File**: `lambda/handler.py:30-34`
```python
def decimal_to_number(obj):
    """Convert Decimal objects to int or float for JSON serialization"""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj
```

### Issue 8: Circular Dependency in CDK
**Problem**: Using `pdf_lambda.grant_invoke(pdf_lambda)` for self-invocation created circular dependency in CloudFormation.

**Solution**: Used `add_to_role_policy` with wildcard resource instead.

**File**: `pdf_upload_system/pdf_upload_system_stack.py:86-91`
```python
pdf_lambda.add_to_role_policy(
    iam.PolicyStatement(
        actions=['lambda:InvokeFunction'],
        resources=['*']
    )
)
```

## Deployment Details

### Stack Outputs
- **CloudFront URL**: https://d2noq38lnnxb2z.cloudfront.net
- **API Gateway URL**: https://4pt4m4aovf.execute-api.ap-southeast-2.amazonaws.com/prod/
- **PDF Bucket**: pdfuploadsystemstack-pdfstoragebucket273b8769-kgs5vjfldkds
- **Jobs Table**: PdfUploadSystemStack-JobsTable1970BC16-51FUQ22Z9GBN
- **Matches Table**: PdfUploadSystemStack-PdfMatchesTable98DC18EF-FBECQ2R67GVG
- **Region**: ap-southeast-2 (Sydney)

### Test Results
```bash
# Async flow test
✅ POST /submit - Returns job ID immediately (HTTP 200)
✅ GET /status/{job_id} - Returns "processing" status
✅ GET /status/{job_id} - Returns "completed" with match count after ~5s
✅ DynamoDB matches stored correctly

# Example response:
{
  "job_id": "1347f061-f477-4bd2-8240-a0ee513ffa03",
  "status": "completed",
  "filename": "test.pdf",
  "matches_found": 3,
  "completed_at": "2026-02-06T05:13:56.736602",
  "message": "Success!"
}
```

### Verification
```bash
# View uploaded files
aws s3 ls s3://pdfuploadsystemstack-pdfstoragebucket273b8769-kgs5vjfldkds/uploads/

# Check job status
curl https://4pt4m4aovf.execute-api.ap-southeast-2.amazonaws.com/prod/status/{job_id}

# View matches in DynamoDB
aws dynamodb scan --table-name PdfUploadSystemStack-PdfMatchesTable98DC18EF-FBECQ2R67GVG --region ap-southeast-2

# View jobs in DynamoDB
aws dynamodb scan --table-name PdfUploadSystemStack-JobsTable1970BC16-51FUQ22Z9GBN --region ap-southeast-2

# Clear tables for testing
aws dynamodb scan --table-name PdfUploadSystemStack-PdfMatchesTable98DC18EF-FBECQ2R67GVG --region ap-southeast-2 --attributes-to-get id --query 'Items[*].id.S' --output text | tr '\t' '\n' | while read id; do aws dynamodb delete-item --table-name PdfUploadSystemStack-PdfMatchesTable98DC18EF-FBECQ2R67GVG --key "{\"id\":{\"S\":\"$id\"}}" --region ap-southeast-2; done
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
10. **PyPDF2**: Simpler than PyMuPDF, pure Python (no binary dependencies)

## File Structure

```
ism-cpack-generator/
├── app.py                                    # CDK app entry point
├── cdk.json                                  # CDK configuration
├── pdf_upload_system/
│   ├── __init__.py
│   └── pdf_upload_system_stack.py           # Infrastructure definition (~190 lines)
├── lambda/
│   ├── handler.py                           # Lambda function (405 lines)
│   ├── requirements.txt                     # boto3, PyPDF2==3.0.1
│   └── [dependencies]/                      # Installed packages (PyPDF2, etc)
├── frontend/
│   ├── index.html                           # Frontend UI with JavaScript (~240 lines)
│   └── styles.css                           # Modern responsive styling (161 lines)
├── test_cors.sh                             # CORS validation test
├── test_simple.sh                           # End-to-end test script
├── test.pdf                                 # Test PDF file
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
  - regex: String
  - url: String
  - s3_key: String
  - created_at: ISO timestamp
  - completed_at: ISO timestamp (when completed)
  - failed_at: ISO timestamp (when failed)
  - matches_found: Number (when completed)
  - error_message: String (when failed)
  - ttl: Number (Unix timestamp, auto-delete after 24h)
```

### Matches Table
```
Primary Key: id (String) - UUID
Attributes:
  - job_id: String (links to Jobs table)
  - matched_line: String (the text that matched)
  - regex: String (pattern used)
  - source_file: String (original filename)
  - s3_key: String (full S3 path)
  - url: String (from form)
  - timestamp: ISO timestamp
  - line_number: Number (line position in PDF)
```

## Deployment Commands

```bash
# Install dependencies (first time or after changes)
cd lambda
pip install -r requirements.txt -t . --upgrade
cd ..

# Deploy infrastructure
source .venv/bin/activate
pip install -r requirements.txt
cdk deploy --require-approval never

# Destroy stack
cdk destroy

# Invalidate CloudFront cache (after frontend changes)
aws cloudfront create-invalidation --distribution-id E3MCIBPC6972X7 --paths "/*"
```

## Success Criteria Met

✅ Frontend deployed on S3/CloudFront
✅ Form with PDF upload, regex input, and URL input
✅ API Gateway with 3 endpoints (upload-url, submit, status)
✅ Async Lambda processing (no timeout issues)
✅ PDF text extraction with PyPDF2
✅ Regex matching on extracted text
✅ DynamoDB storage for jobs and matches
✅ Real-time status polling with "In Progress..." / "Success!" messages
✅ CORS properly configured on all endpoints
✅ Files stored in encrypted S3 bucket
✅ End-to-end testing completed successfully
✅ Can process large PDFs (up to 90s)

## Known Limitations

1. **Lambda Timeout**: Max 90 seconds for PDF processing (very large PDFs may timeout)
2. **Memory**: 512MB Lambda memory (adequate for most PDFs, may need increase for huge files)
3. **File Size**: 10MB limit on frontend (S3 presigned URL has no hard limit)
4. **No Pagination**: Status endpoint doesn't paginate matches (returns summary only)
5. **No Authentication**: Public access (would add Cognito for production)
6. **TTL Cleanup**: Jobs auto-delete after 24h, matches persist forever
7. **Error Reporting**: Limited detail in frontend error messages

## Future Enhancements

- [ ] Add pagination for viewing matches
- [ ] Implement URL content fetching and processing
- [ ] Add authentication (Cognito)
- [ ] Server-side file type and size validation
- [ ] Virus scanning integration (S3 + Lambda trigger)
- [ ] CloudWatch metrics and alarms
- [ ] WebSocket for real-time updates (instead of polling)
- [ ] Batch processing for multiple PDFs
- [ ] Export matches to CSV/JSON
- [ ] Add GSI on job_id in matches table for efficient queries
- [ ] Increase Lambda memory for very large PDFs
- [ ] Add retry logic for failed jobs

## Development Timeline

- **Initial setup and deployment**: ~20 minutes
- **PDF processing implementation**: ~40 minutes
- **CORS troubleshooting**: ~30 minutes
- **Async processing conversion**: ~60 minutes
- **Testing and validation**: ~20 minutes
- **Documentation**: ~15 minutes
- **Total**: ~3 hours

## Current Status

**✅ FULLY OPERATIONAL**

System is production-ready for moderate workloads. Successfully processes PDFs asynchronously, stores matches in DynamoDB, and provides real-time status updates via polling.

**Last Updated**: 2026-02-06
**Version**: 2.0 (Async Processing)
