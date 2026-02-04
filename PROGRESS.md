# PDF Upload System - Development Progress

## Project Overview
A serverless PDF upload system built with AWS CDK, featuring a CloudFront-hosted frontend that allows users to upload PDFs with metadata (regex pattern and URL) via presigned S3 URLs.

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
       ├─── POST /upload-url  ──> Lambda (Generate presigned URL)
       └─── POST /submit      ──> Lambda (Accept form submission)
                                      │
                                      v
                               ┌──────────────┐
                               │  S3 Bucket   │ (PDF Storage)
                               └──────────────┘
```

## Components

### Infrastructure (CDK)
- **Frontend S3 Bucket**: Private bucket served via CloudFront with OAI
- **PDF Storage S3 Bucket**: Private, encrypted bucket with CORS for presigned uploads
- **CloudFront Distribution**: CDN with HTTPS redirect
- **API Gateway**: REST API with CORS enabled
- **Lambda Function**: Python 3.11 handler with S3 presigned URL generation
- **IAM Roles**: Automatic permissions via CDK grants

### Frontend
- **Location**: `frontend/index.html` + `frontend/styles.css`
- **Features**:
  - PDF file upload (max 10MB)
  - Regex pattern input
  - URL input
  - Green success message on completion
  - Loading states and error handling
  - Direct browser-to-S3 upload via presigned URLs

### Backend
- **Location**: `lambda/handler.py`
- **Runtime**: Python 3.11
- **Endpoints**:
  1. `POST /upload-url` - Generates presigned S3 POST URL
  2. `POST /submit` - Processes form submission and returns filename
- **Key Features**:
  - AWS Signature Version 4
  - Regional S3 endpoint usage
  - Unique filename generation with timestamps
  - 5-minute presigned URL expiration

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

**File**: `lambda/handler.py:8-13`
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

**File**: `pdf_upload_system/pdf_upload_system_stack.py:31`
```python
exposed_headers=["ETag", "x-amz-server-side-encryption", "x-amz-request-id", "x-amz-id-2"]
```

## Deployment Details

### Stack Outputs
- **CloudFront URL**: https://d2noq38lnnxb2z.cloudfront.net
- **API Gateway URL**: https://4pt4m4aovf.execute-api.ap-southeast-2.amazonaws.com/prod/
- **PDF Bucket**: pdfuploadsystemstack-pdfstoragebucket273b8769-kgs5vjfldkds
- **Region**: ap-southeast-2 (Sydney)

### Test Results
```bash
✅ Step 1: Get presigned URL - SUCCESS (HTTP 200)
✅ Step 2: Upload to S3 - SUCCESS (HTTP 204)
✅ Step 3: Submit form - SUCCESS (HTTP 200)
```

### Verification
```bash
# View uploaded files
aws s3 ls s3://pdfuploadsystemstack-pdfstoragebucket273b8769-kgs5vjfldkds/uploads/

# Test upload
./test_upload.sh
```

## Key Technical Decisions

1. **Presigned URLs**: Direct browser-to-S3 uploads avoid Lambda payload limits (6MB) and reduce costs
2. **AWS Signature v4**: Required for regional endpoints and modern security standards
3. **Path-style addressing**: Ensures consistent regional endpoint usage
4. **CloudFront OAI**: Secure frontend hosting without public S3 bucket access
5. **Python 3.11**: Latest stable Lambda runtime with good performance
6. **No database**: Stateless design keeps costs low for this proof of concept

## File Structure

```
pdf-upload-system/
├── app.py                                    # CDK app entry point
├── cdk.json                                  # CDK configuration
├── pdf_upload_system/
│   ├── __init__.py
│   └── pdf_upload_system_stack.py           # Infrastructure definition (148 lines)
├── lambda/
│   ├── handler.py                           # Lambda function (127 lines)
│   └── requirements.txt                     # boto3
├── frontend/
│   ├── index.html                           # Frontend UI with JavaScript (166 lines)
│   └── styles.css                           # Modern responsive styling (161 lines)
├── test_upload.sh                           # Test harness script
├── requirements.txt                         # CDK dependencies
└── PROGRESS.md                              # This file

Total: ~602 lines of code
```

## Next Steps (Future Enhancements)

- Add DynamoDB table to store submission metadata
- Implement actual PDF processing with regex search
- Fetch and process content from submitted URLs
- Add authentication (Cognito)
- Server-side file type and size validation
- Virus scanning integration
- CloudWatch metrics and alarms

## Deployment Commands

```bash
# Deploy infrastructure
cd pdf-upload-system
source .venv/bin/activate
pip install -r requirements.txt
cdk deploy

# Destroy stack
cdk destroy

# Invalidate CloudFront cache
aws cloudfront create-invalidation --distribution-id E3MCIBPC6972X7 --paths "/*"
```

## Success Criteria Met

✅ Frontend deployed on S3/CloudFront
✅ Form with PDF upload, regex input, and URL input
✅ API Gateway calling Lambda function
✅ Lambda returns uploaded filename
✅ Green success message displayed
✅ Files stored in encrypted S3 bucket
✅ End-to-end testing completed successfully

## Development Time

- Initial setup and deployment: ~20 minutes
- Issue troubleshooting and fixes: ~40 minutes
- Testing and validation: ~10 minutes
- **Total**: ~70 minutes
