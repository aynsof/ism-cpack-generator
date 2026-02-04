#!/bin/bash
set -e

API_URL="https://4pt4m4aovf.execute-api.ap-southeast-2.amazonaws.com/prod/"
ORIGIN="https://d2noq38lnnxb2z.cloudfront.net"

echo "============================================================"
echo "Testing PDF Upload System"
echo "============================================================"

# Create a test PDF
cat > /tmp/test.pdf << 'EOF'
%PDF-1.4
1 0 obj
<<
/Type /Catalog
/Pages 2 0 R
>>
endobj
2 0 obj
<<
/Type /Pages
/Kids [3 0 R]
/Count 1
>>
endobj
3 0 obj
<<
/Type /Page
/Parent 2 0 R
/MediaBox [0 0 612 792]
/Contents 4 0 R
/Resources <<
/Font <<
/F1 <<
/Type /Font
/Subtype /Type1
/BaseFont /Helvetica
>>
>>
>>
>>
endobj
4 0 obj
<<
/Length 44
>>
stream
BT
/F1 12 Tf
100 700 Td
(Test PDF) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000317 00000 n
trailer
<<
/Size 5
/Root 1 0 R
>>
startxref
410
%%EOF
EOF

echo ""
echo "[Step 1] Getting presigned URL..."
PRESIGNED_RESPONSE=$(curl -s -X POST "${API_URL}upload-url" \
  -H "Content-Type: application/json" \
  -H "Origin: ${ORIGIN}" \
  -d '{"filename":"test.pdf"}')

echo "Response: $PRESIGNED_RESPONSE"

# Save response to file for easier parsing
echo "$PRESIGNED_RESPONSE" > /tmp/presigned_response.json

# Extract values
UPLOAD_URL=$(python3 -c "import sys, json; print(json.load(open('/tmp/presigned_response.json'))['uploadUrl'])")
KEY=$(python3 -c "import sys, json; print(json.load(open('/tmp/presigned_response.json'))['fields']['key'])")

echo "Upload URL: $UPLOAD_URL"
echo "Key: $KEY"

# Build curl command dynamically with all fields
echo ""
echo "[Step 2] Uploading to S3..."

# Extract all fields and build form data
FIELDS=$(python3 -c "
import json
data = json.load(open('/tmp/presigned_response.json'))
fields = data['fields']
for key, value in fields.items():
    print(f'{key}={value}')
")

# Build curl command
CURL_CMD="curl -v -X POST \"$UPLOAD_URL\" -H \"Origin: ${ORIGIN}\""

while IFS='=' read -r key value; do
    CURL_CMD="$CURL_CMD -F \"$key=$value\""
done <<< "$FIELDS"

CURL_CMD="$CURL_CMD -F \"file=@/tmp/test.pdf;type=application/pdf\""

# Execute
eval "$CURL_CMD" > /tmp/s3_response.txt 2>&1

cat /tmp/s3_response.txt

if grep -q "HTTP.*20[04]" /tmp/s3_response.txt; then
  echo ""
  echo "✅ Upload successful!"

  echo ""
  echo "[Step 3] Submitting form..."
  SUBMIT_RESPONSE=$(curl -s -X POST "${API_URL}submit" \
    -H "Content-Type: application/json" \
    -H "Origin: ${ORIGIN}" \
    -d "{\"filename\":\"test.pdf\",\"regex\":\"^test.*\",\"url\":\"https://example.com\"}")

  echo "Response: $SUBMIT_RESPONSE"
  echo ""
  echo "✅ SUCCESS!"
else
  echo ""
  echo "❌ Upload failed!"
  echo "Checking for error message..."
  grep -A5 "<Message>" /tmp/s3_response.txt || echo "No error message found"
fi
