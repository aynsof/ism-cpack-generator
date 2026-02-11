# AWS Config Conformance Pack Generator

This standalone Python script generates deployable AWS Config Conformance Packs from the ISM Controls mapping data stored in DynamoDB.

## What It Does

1. **Reads DynamoDB**: Scans the ConfigMappingsTable to extract all unique AWS Config Rules
2. **Fetches Documentation**: Downloads the latest AWS Config Rules documentation
3. **Uses Bedrock AI**: For each Config Rule, queries Claude Opus 4.5 to extract:
   - Proper rule name and format
   - Required and optional parameters
   - Default parameter values
   - Rule descriptions
4. **Generates Conformance Packs**: Creates one or more YAML conformance pack files, respecting AWS limits:
   - Max 51,200 bytes per pack
   - Max 130 rules per pack
5. **Outputs**: YAML files ready to deploy + detailed generation report

## Prerequisites

```bash
# Install dependencies
pip install -r conformance-pack-requirements.txt

# AWS credentials configured with access to:
# - DynamoDB (read ConfigMappingsTable)
# - Bedrock (invoke Claude Opus 4.5)
```

## Usage

### Basic Usage

```bash
python generate_conformance_packs.py
```

This will:
- Read from DynamoDB table in ap-southeast-2
- Output conformance packs to `./conformance-packs/`
- Use prefix `ism-controls` for pack names

### Advanced Usage

```bash
# Custom output directory and prefix
python generate_conformance_packs.py \\
  --output-dir ./my-packs \\
  --prefix my-org-ism

# Cache documentation locally (speeds up repeated runs)
python generate_conformance_packs.py --cache-docs
```

### Command-Line Options

- `--output-dir`: Output directory for conformance pack files (default: `./conformance-packs`)
- `--prefix`: Prefix for conformance pack names (default: `ism-controls`)
- `--cache-docs`: Cache AWS Config Rules documentation locally to avoid repeated downloads

## Output Files

The script generates:

1. **Conformance Pack YAML files**: `conformance-pack-{prefix}-{number}.yaml`
   - Ready to deploy to AWS Config
   - Contains properly formatted Config Rules with parameters
   - Split across multiple files if needed (due to size limits)

2. **Generation Report**: `GENERATION_REPORT.md`
   - Summary of all generated packs
   - File sizes and rule counts
   - Deployment commands for each pack
   - List of any failed rules

3. **Cached Documentation** (optional): `config-rules-docs.html`
   - Local cache of AWS Config Rules documentation
   - Only created when using `--cache-docs` flag

## Example Output Structure

```
conformance-packs/
├── conformance-pack-ism-controls-01.yaml    # First 130 rules or ~50KB
├── conformance-pack-ism-controls-02.yaml    # Next batch if needed
├── conformance-pack-ism-controls-03.yaml    # And so on...
├── GENERATION_REPORT.md                     # Summary and deploy commands
└── config-rules-docs.html                   # Cached docs (if --cache-docs used)
```

## Deploying Conformance Packs

After generation, deploy to AWS Config:

```bash
# Deploy a single pack
aws configservice put-conformance-pack \\
  --conformance-pack-name ism-controls-01 \\
  --conformance-pack-input-parameters file://conformance-packs/conformance-pack-ism-controls-01.yaml

# Deploy all packs
for pack in conformance-packs/conformance-pack-*.yaml; do
  name=$(basename "$pack" .yaml | sed 's/conformance-pack-//')
  echo "Deploying $name..."
  aws configservice put-conformance-pack \\
    --conformance-pack-name "$name" \\
    --conformance-pack-input-parameters "file://$pack"
done
```

## How It Works

### 1. DynamoDB Scan
Scans the ConfigMappingsTable to build a mapping:
```
{
  "CLOUDWATCH_LOG_GROUP_ENCRYPTED": ["ism-1984", "ism-1985"],
  "S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED": ["ism-0459", "ism-0460"],
  ...
}
```

### 2. Bedrock Query (Per Rule)
For each unique Config Rule, sends a prompt to Claude Opus 4.5:
- Provides the rule identifier
- Includes AWS Config Rules documentation
- Requests structured JSON response with:
  - Rule name and format
  - Parameters and defaults
  - Description

### 3. YAML Generation
Formats each rule into conformance pack structure:
```yaml
Resources:
  ISMConfigRule001:
    Type: AWS::Config::ConfigRule
    Properties:
      ConfigRuleName: CLOUDWATCH_LOG_GROUP_ENCRYPTED
      Description: Checks whether CloudWatch Log Groups are encrypted
      Source:
        Owner: AWS
        SourceIdentifier: CLOUDWATCH_LOG_GROUP_ENCRYPTED
      InputParameters: '{"kmsKeyId": "optional-key-id"}'
```

### 4. Pack Splitting
Monitors cumulative size and rule count, splitting into multiple packs when limits approached:
- Tracks YAML byte size (max 51,200 bytes)
- Counts rules per pack (max 130)
- Creates sequential packs: `-01`, `-02`, `-03`, etc.

## Cost Considerations

**Bedrock API Costs (Claude Opus 4.5)**:
- ~200K input tokens per rule (includes full AWS Config docs)
- ~100-200 output tokens per rule
- At $15 per million input tokens: ~$3 per 1000 rules
- Typical cost for full ISM mapping: $3-5

**Optimization Tips**:
- Use `--cache-docs` to avoid re-downloading documentation
- Run script once, deploy conformance packs multiple times
- Consider using Claude Sonnet 4.5 for lower cost (update `BEDROCK_MODEL_ID` in script)

## Customization

### Change AWS Region

Edit the script constants:
```python
REGION = 'us-east-1'  # Your region
```

### Change DynamoDB Table

Edit the script constants:
```python
CONFIG_MAPPINGS_TABLE = 'your-table-name'
```

### Use Different Bedrock Model

Edit the script constants:
```python
# Use Sonnet for lower cost
BEDROCK_MODEL_ID = 'global.anthropic.claude-sonnet-4-5-20250928-v1:0'
```

### Adjust Pack Size Limits

Edit the script constants:
```python
MAX_PACK_SIZE_BYTES = 40000  # More conservative limit
MAX_RULES_PER_PACK = 100     # Fewer rules per pack
```

## Troubleshooting

### "AccessDeniedException" from Bedrock
**Problem**: IAM user/role doesn't have Bedrock access

**Solution**:
```bash
# Add Bedrock permissions to your IAM role/user
aws iam put-user-policy --user-name your-user --policy-name BedrockAccess --policy-document '{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "bedrock:InvokeModel",
    "Resource": "arn:aws:bedrock:*::foundation-model/anthropic.claude-opus-*"
  }]
}'
```

### "ResourceNotFoundException" for DynamoDB
**Problem**: Table name incorrect or doesn't exist in region

**Solution**:
- Verify table name in AWS Console
- Check region matches (default: ap-southeast-2)
- Update `CONFIG_MAPPINGS_TABLE` constant in script

### "Model not found" Error
**Problem**: Bedrock model ID incorrect or not available in region

**Solution**:
- Use inference profile ID (starts with `global.`)
- Verify model availability: `aws bedrock list-foundation-models`
- Check you have Bedrock access in your account

### Conformance Pack Too Large
**Problem**: Generated YAML exceeds 51,200 bytes but script didn't split

**Solution**:
- Lower `MAX_PACK_SIZE_BYTES` to 45000 for more safety margin
- Reduce rule descriptions to be more concise
- File an issue with specific rule that's too large

## Integration with Existing System

This script is **completely separate** from the main ISM upload system. It:
- Reads from the same DynamoDB table (ConfigMappingsTable)
- Uses the same Bedrock model (Claude Opus 4.5)
- But runs independently, typically after ISM catalog has been processed

**Typical Workflow**:
1. Upload ISM catalog JSON via frontend → creates Config Rules mappings
2. Run this script → generates conformance packs from mappings
3. Deploy conformance packs → enables Config Rules in AWS

## Future Enhancements

- [ ] Add validation to check if Config Rules actually exist in AWS
- [ ] Support for custom Config Rules (Lambda-backed)
- [ ] Parameter templates for common use cases
- [ ] Dry-run mode to estimate costs before Bedrock queries
- [ ] Incremental updates (only process new rules since last run)
- [ ] Support for organizational conformance packs
- [ ] Integration with AWS Organizations for multi-account deployment

## License

Same as parent project.
