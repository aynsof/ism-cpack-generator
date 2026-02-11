#!/bin/bash
# Test harness for conformance pack deployment
# Iterates until successful deployment

set -e

OUTPUT_DIR="./output"
PACK_FILE="$OUTPUT_DIR/conformance-pack-ism-controls.yaml"
PACK_NAME="ism-controls-test"

echo "=========================================="
echo "Conformance Pack Deployment Test Harness"
echo "=========================================="
echo ""

# Check if conformance pack file exists
if [ ! -f "$PACK_FILE" ]; then
    echo "ERROR: Conformance pack file not found: $PACK_FILE"
    echo "Run: python generate_conformance_packs.py --cache-docs --output-dir ./output"
    exit 1
fi

echo "Conformance pack file: $PACK_FILE"
echo "Pack name: $PACK_NAME"
echo ""
echo "Attempting deployment..."
echo ""

# Try to deploy
if aws configservice put-conformance-pack \
    --conformance-pack-name "$PACK_NAME" \
    --template-body "file://$PACK_FILE" 2>&1; then

    echo ""
    echo "=========================================="
    echo "SUCCESS! Conformance pack deployed."
    echo "=========================================="
    echo ""
    echo "Clean up with:"
    echo "  aws configservice delete-conformance-pack --conformance-pack-name $PACK_NAME"
    exit 0
else
    echo ""
    echo "=========================================="
    echo "DEPLOYMENT FAILED"
    echo "=========================================="
    exit 1
fi
