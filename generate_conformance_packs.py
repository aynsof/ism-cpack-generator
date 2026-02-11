#!/usr/bin/env python3
"""
Generate AWS Config Conformance Packs from DynamoDB Config Rules Mappings

This script:
1. Queries DynamoDB ConfigMappingsTable for all unique Config Rules
2. Fetches AWS Config Rules documentation
3. Uses Bedrock (Claude Opus 4.5) to format each rule properly
4. Generates one or more conformance pack YAML files (respecting size limits)

Conformance Pack Limits:
- Max 51,200 bytes per pack
- Max 130 rules per pack (byte limit usually hit first)

Usage:
    python generate_conformance_packs.py [--output-dir ./output] [--prefix ism-controls]
"""

import boto3
import json
import yaml
import os
import sys
import argparse
from typing import List, Dict, Set, Any
from collections import defaultdict
from datetime import datetime
import requests
from io import StringIO

# AWS Configuration
REGION = 'ap-southeast-2'
CONFIG_MAPPINGS_TABLE = 'PdfUploadSystemStack-ConfigMappingsTableECB154B2-6LBWYSLUDXOE'
BEDROCK_MODEL_ID = 'global.anthropic.claude-opus-4-5-20251101-v1:0'
CONFIG_RULES_URL = 'https://docs.aws.amazon.com/config/latest/developerguide/managed-rules-by-aws-config.html'

# Conformance Pack Limits
MAX_PACK_SIZE_BYTES = 51200  # 50 KB
MAX_RULES_PER_PACK = 130

# Initialize AWS clients
dynamodb = boto3.client('dynamodb', region_name=REGION)
bedrock_runtime = boto3.client('bedrock-runtime', region_name=REGION)


def fetch_config_rules_documentation() -> str:
    """Fetch AWS Config Rules documentation from AWS docs"""
    print(f"Fetching AWS Config Rules documentation from {CONFIG_RULES_URL}...")
    try:
        response = requests.get(CONFIG_RULES_URL, timeout=30)
        response.raise_for_status()
        print(f"✓ Fetched documentation ({len(response.text)} bytes)")
        return response.text
    except Exception as e:
        print(f"✗ Error fetching documentation: {e}")
        sys.exit(1)


def get_unique_config_rules() -> Dict[str, List[str]]:
    """
    Scan DynamoDB ConfigMappingsTable and extract unique Config Rules
    Returns dict: {rule_identifier: [control_id1, control_id2, ...]}
    """
    print(f"\nScanning DynamoDB table: {CONFIG_MAPPINGS_TABLE}...")

    rules_to_controls = defaultdict(list)

    try:
        # TEMPORARY: Limit to 10 items for testing
        TEST_LIMIT = 10

        paginator = dynamodb.get_paginator('scan')
        page_iterator = paginator.paginate(
            TableName=CONFIG_MAPPINGS_TABLE,
            PaginationConfig={'MaxItems': TEST_LIMIT}
        )

        item_count = 0
        for page in page_iterator:
            for item in page.get('Items', []):
                item_count += 1
                rule_id = item.get('config_rule_identifier', {}).get('S', '')
                control_id = item.get('control_id', {}).get('S', '')

                if rule_id and control_id:
                    rules_to_controls[rule_id].append(control_id)

                # TEMPORARY: Stop after TEST_LIMIT items
                if item_count >= TEST_LIMIT:
                    break

            if item_count >= TEST_LIMIT:
                break

        unique_rules = len(rules_to_controls)
        print(f"✓ Found {item_count} mappings covering {unique_rules} unique Config Rules")

        return dict(rules_to_controls)

    except Exception as e:
        print(f"✗ Error scanning DynamoDB: {e}")
        sys.exit(1)


def query_bedrock_for_rule_format(rule_identifier: str, controls: List[str],
                                   docs_content: str) -> Dict[str, Any]:
    """
    Query Bedrock to extract proper Config Rule formatting from documentation

    Returns dict with:
    - ConfigRuleName: str
    - Source: dict with Owner and SourceIdentifier
    - InputParameters: dict (if any)
    - Description: str
    """
    print(f"  Querying Bedrock for {rule_identifier}...", end='', flush=True)

    prompt = f"""You are analyzing AWS Config Rules documentation to extract the proper conformance pack format for a specific rule.

TASK: Extract the conformance pack configuration for the AWS Config Rule: {rule_identifier}

This rule maps to the following ISM controls: {', '.join(controls[:5])}{'...' if len(controls) > 5 else ''}

REFERENCE DOCUMENTATION:
{docs_content[:100000]}

INSTRUCTIONS:
1. Find the rule "{rule_identifier}" in the documentation
2. Extract the following information:
   - Rule name (usually the identifier in uppercase with dashes/underscores)
   - Source owner (should be "AWS" for managed rules)
   - Source identifier (usually same as rule name)
   - Required parameters (if any) with their types and descriptions
   - Optional parameters (if any) with defaults
   - Brief description of what the rule checks

3. Format your response as JSON with this structure:
{{
  "ConfigRuleName": "RULE_NAME",
  "Description": "Brief description of what this rule checks",
  "Source": {{
    "Owner": "AWS",
    "SourceIdentifier": "SOURCE_IDENTIFIER"
  }},
  "InputParameters": {{
    "parameterName": "defaultValue or null if required"
  }},
  "ISMControls": ["control-id-1", "control-id-2"]
}}

IMPORTANT:
- If no parameters are needed, set InputParameters to an empty object {{}}
- For required parameters without defaults, use null as the value
- Use reasonable defaults for optional parameters based on common use cases
- ConfigRuleName should match AWS naming conventions (UPPER_CASE_WITH_UNDERSCORES)
- If you cannot find the rule in documentation, return {{"error": "Rule not found in documentation"}}

Return ONLY the JSON object, no additional text."""

    try:
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "temperature": 0.0,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        }

        response = bedrock_runtime.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(request_body)
        )

        response_body = json.loads(response['body'].read())
        content = response_body['content'][0]['text']

        # Parse JSON from response
        # Sometimes Claude wraps JSON in markdown code blocks
        content = content.strip()
        if content.startswith('```'):
            # Remove markdown code blocks
            lines = content.split('\n')
            content = '\n'.join(lines[1:-1]) if len(lines) > 2 else content
            content = content.replace('```json', '').replace('```', '').strip()

        rule_config = json.loads(content)

        # Add ISM controls to the config
        rule_config['ISMControls'] = controls

        print(f" ✓")
        return rule_config

    except json.JSONDecodeError as e:
        print(f" ✗ JSON parse error: {e}")
        print(f"Response content: {content[:200]}...")
        return {"error": f"Failed to parse Bedrock response: {e}"}
    except Exception as e:
        print(f" ✗ {e}")
        return {"error": str(e)}


def to_pascal_case(text: str) -> str:
    """
    Convert text to PascalCase
    Handles: 'my-rule-name' -> 'MyRuleName', 'MY_RULE_NAME' -> 'MyRuleName'
    Also handles camelCase input: 'myRuleName' -> 'MyRuleName' (just uppercase first letter)
    """
    # If no separators and starts with lowercase, assume camelCase - just uppercase first letter
    if '-' not in text and '_' not in text and text and text[0].islower():
        return text[0].upper() + text[1:]

    # Otherwise, split on separators and capitalize each word
    return ''.join(word.capitalize() for word in text.replace('_', '-').split('-'))


def create_conformance_pack(rules: List[Dict[str, Any]], pack_name: str) -> str:
    """
    Create a conformance pack YAML from a list of rule configs

    Format follows AWS Conformance Pack structure:
    - Parameters: Define configurable inputs with defaults
    - Conditions: Check if parameters are non-empty
    - Resources: Config Rules that conditionally use parameters
    """
    parameters = {}
    conditions = {}
    resources = {}

    # Process each rule
    for i, rule_config in enumerate(rules, 1):
        if "error" in rule_config:
            continue

        rule_name = rule_config.get("ConfigRuleName", f"ISMConfigRule{i:03d}")
        description = rule_config.get("Description", "AWS Config Rule")
        source = rule_config.get("Source", {
            "Owner": "AWS",
            "SourceIdentifier": rule_name
        })

        # Create resource name (use rule name for clarity)
        resource_name = to_pascal_case(rule_name)

        # Build the rule properties
        rule_properties = {
            "ConfigRuleName": rule_name,
            "Description": description,
            "Source": source
        }

        # Handle input parameters
        input_params = rule_config.get("InputParameters", {})
        if input_params:
            # Filter out null values (required params without defaults)
            filtered_params = {k: v for k, v in input_params.items() if v is not None}

            if filtered_params:
                input_params_yaml = {}

                for param_name, param_value in filtered_params.items():
                    # Create parameter name: RuleNameParamParameterName (PascalCase)
                    param_key = f"{resource_name}Param{to_pascal_case(param_name)}"
                    # Condition name is same as parameter but with first letter lowercase (camelCase)
                    condition_key = param_key[0].lower() + param_key[1:] if param_key else param_key

                    # Add to Parameters section
                    parameters[param_key] = {
                        "Default": str(param_value),
                        "Type": "String"
                    }

                    # Add to Conditions section
                    conditions[condition_key] = {
                        "Fn::Not": [
                            {
                                "Fn::Equals": [
                                    "",
                                    {"Ref": param_key}
                                ]
                            }
                        ]
                    }

                    # Add conditional parameter reference in InputParameters
                    input_params_yaml[param_name] = {
                        "Fn::If": [
                            condition_key,
                            {"Ref": param_key},
                            {"Ref": "AWS::NoValue"}
                        ]
                    }

                rule_properties["InputParameters"] = input_params_yaml

        # Add to resources
        resources[resource_name] = {
            "Properties": rule_properties,
            "Type": "AWS::Config::ConfigRule"
        }

    # Build conformance pack structure
    conformance_pack = {}

    if parameters:
        conformance_pack["Parameters"] = parameters

    if conditions:
        conformance_pack["Conditions"] = conditions

    conformance_pack["Resources"] = resources

    # Generate YAML with proper formatting
    yaml_output = yaml.dump(
        conformance_pack,
        default_flow_style=False,
        sort_keys=False,
        width=120,
        indent=2
    )

    # Add header comment
    header = f"""# AWS Config Conformance Pack: {pack_name}
# Generated: {datetime.utcnow().isoformat()}Z
# Rules: {len(resources)}
# Source: ISM Controls Mapping via Amazon Bedrock
#
# Deploy with:
#   aws configservice put-conformance-pack --conformance-pack-name {pack_name} --template-body file://conformance-pack-{pack_name}.yaml

"""

    return header + yaml_output


def split_into_packs(rule_configs: List[Dict[str, Any]],
                     prefix: str = "ism-controls") -> List[tuple[str, List[Dict[str, Any]]]]:
    """
    Split rules into multiple conformance packs respecting size limits

    Returns list of (pack_name, rules) tuples
    """
    packs = []
    current_pack = []
    current_size = 0
    pack_number = 1

    for rule_config in rule_configs:
        if "error" in rule_config:
            continue

        # Estimate size of this rule in YAML (including parameters and conditions)
        # Each rule with parameters generates ~3 sections: parameter def, condition, and resource
        rule_name = rule_config.get("ConfigRuleName", "UNKNOWN_RULE")
        description = rule_config.get("Description", "")
        input_params = rule_config.get("InputParameters", {})

        # Base size estimate for rule definition
        rule_size = len(rule_name) + len(description) + 200  # 200 bytes for YAML structure

        # Add size for each parameter (parameter def + condition + reference)
        for param_name, param_value in input_params.items():
            if param_value is not None:
                # Each parameter adds ~150 bytes (parameter, condition, and reference)
                rule_size += 150 + len(param_name) + len(str(param_value))

        # Check if adding this rule would exceed limits
        if (len(current_pack) >= MAX_RULES_PER_PACK or
            current_size + rule_size > MAX_PACK_SIZE_BYTES * 0.9):  # 90% threshold for safety

            # Save current pack and start new one
            if current_pack:
                pack_name = f"{prefix}-{pack_number:02d}"
                packs.append((pack_name, current_pack))
                pack_number += 1
                current_pack = []
                current_size = 0

        current_pack.append(rule_config)
        current_size += rule_size

    # Add final pack
    if current_pack:
        if pack_number == 1:
            pack_name = prefix
        else:
            pack_name = f"{prefix}-{pack_number:02d}"
        packs.append((pack_name, current_pack))

    return packs


def generate_summary_report(rule_configs: List[Dict[str, Any]],
                           packs: List[tuple[str, List[Dict[str, Any]]]],
                           output_dir: str) -> str:
    """
    Generate a summary report of the conformance packs
    """
    total_rules = len([r for r in rule_configs if "error" not in r])
    failed_rules = len([r for r in rule_configs if "error" in r])

    report = f"""# ISM Controls Conformance Pack Generation Report

Generated: {datetime.utcnow().isoformat()}Z

## Summary

- Total unique Config Rules: {len(rule_configs)}
- Successfully processed: {total_rules}
- Failed to process: {failed_rules}
- Conformance packs generated: {len(packs)}

## Conformance Packs

"""

    for pack_name, rules in packs:
        pack_file = f"conformance-pack-{pack_name}.yaml"
        pack_path = os.path.join(output_dir, pack_file)

        # Calculate actual file size
        if os.path.exists(pack_path):
            file_size = os.path.getsize(pack_path)
            size_pct = (file_size / MAX_PACK_SIZE_BYTES) * 100
        else:
            file_size = 0
            size_pct = 0

        report += f"""### {pack_name}
- File: `{pack_file}`
- Rules: {len(rules)}
- Size: {file_size:,} bytes ({size_pct:.1f}% of limit)
- Deploy command:
  ```bash
  aws configservice put-conformance-pack \\
    --conformance-pack-name {pack_name} \\
    --conformance-pack-input-parameters file://{pack_path}
  ```

"""

    # Add failed rules section
    if failed_rules > 0:
        report += "\n## Failed Rules\n\n"
        for rule_config in rule_configs:
            if "error" in rule_config:
                rule_id = rule_config.get("ConfigRuleName", "UNKNOWN")
                error = rule_config.get("error", "Unknown error")
                report += f"- **{rule_id}**: {error}\n"

    return report


def main():
    parser = argparse.ArgumentParser(
        description='Generate AWS Config Conformance Packs from ISM Controls mappings'
    )
    parser.add_argument(
        '--output-dir',
        default='./conformance-packs',
        help='Output directory for conformance pack files (default: ./conformance-packs)'
    )
    parser.add_argument(
        '--prefix',
        default='ism-controls',
        help='Prefix for conformance pack names (default: ism-controls)'
    )
    parser.add_argument(
        '--cache-docs',
        action='store_true',
        help='Cache AWS Config Rules documentation locally'
    )

    args = parser.parse_args()

    # Validate arguments
    errors = []

    # Validate output directory
    if not args.output_dir or args.output_dir.strip() == '':
        errors.append("Output directory cannot be empty")
    elif len(args.output_dir) > 255:
        errors.append("Output directory path too long (max 255 characters)")

    # Validate prefix
    if not args.prefix or args.prefix.strip() == '':
        errors.append("Prefix cannot be empty")
    elif len(args.prefix) > 100:
        errors.append("Prefix too long (max 100 characters)")
    elif not all(c.isalnum() or c in '-_' for c in args.prefix):
        errors.append("Prefix must contain only alphanumeric characters, hyphens, and underscores")

    # Check if prefix would make valid conformance pack name
    # AWS conformance pack names: 1-256 chars, alphanumeric and hyphens
    test_pack_name = f"{args.prefix}-01"
    if len(test_pack_name) > 256:
        errors.append(f"Prefix too long - conformance pack name '{test_pack_name}' exceeds 256 characters")

    if errors:
        print("ERROR: Invalid arguments:")
        for error in errors:
            print(f"  ✗ {error}")
        print("\nRun with --help for usage information")
        sys.exit(1)

    print("=" * 80)
    print("AWS Config Conformance Pack Generator")
    print("ISM Controls → AWS Config Rules Mapping")
    print("=" * 80)

    # Create output directory
    try:
        os.makedirs(args.output_dir, exist_ok=True)
    except PermissionError:
        print(f"\n✗ ERROR: Permission denied creating directory: {args.output_dir}")
        sys.exit(1)
    except OSError as e:
        print(f"\n✗ ERROR: Cannot create output directory: {e}")
        sys.exit(1)

    print(f"\nOutput directory: {args.output_dir}")

    # Step 1: Fetch documentation
    docs_cache_file = os.path.join(args.output_dir, 'config-rules-docs.html')
    if args.cache_docs and os.path.exists(docs_cache_file):
        print(f"Using cached documentation from {docs_cache_file}")
        with open(docs_cache_file, 'r') as f:
            docs_content = f.read()
    else:
        docs_content = fetch_config_rules_documentation()
        if args.cache_docs:
            with open(docs_cache_file, 'w') as f:
                f.write(docs_content)
            print(f"✓ Cached documentation to {docs_cache_file}")

    # Step 2: Get unique Config Rules from DynamoDB
    rules_to_controls = get_unique_config_rules()

    if not rules_to_controls:
        print("\n✗ No Config Rules found in DynamoDB table")
        sys.exit(1)

    print(f"\nProcessing {len(rules_to_controls)} unique Config Rules...")
    print("-" * 80)

    # Step 3: Query Bedrock for each rule
    rule_configs = []
    for i, (rule_id, controls) in enumerate(rules_to_controls.items(), 1):
        print(f"[{i}/{len(rules_to_controls)}] {rule_id} (maps to {len(controls)} controls)")

        rule_config = query_bedrock_for_rule_format(rule_id, controls, docs_content)
        rule_configs.append(rule_config)

    print("-" * 80)

    # Step 4: Split into conformance packs
    print("\nSplitting rules into conformance packs...")
    packs = split_into_packs(rule_configs, args.prefix)
    print(f"✓ Created {len(packs)} conformance pack(s)")

    # Step 5: Write conformance pack YAML files
    print("\nWriting conformance pack files...")
    for pack_name, rules in packs:
        pack_file = f"conformance-pack-{pack_name}.yaml"
        pack_path = os.path.join(args.output_dir, pack_file)

        pack_yaml = create_conformance_pack(rules, pack_name)

        with open(pack_path, 'w') as f:
            f.write(pack_yaml)

        file_size = os.path.getsize(pack_path)
        print(f"  ✓ {pack_file} ({len(rules)} rules, {file_size:,} bytes)")

    # Step 6: Generate summary report
    print("\nGenerating summary report...")
    report = generate_summary_report(rule_configs, packs, args.output_dir)
    report_path = os.path.join(args.output_dir, 'GENERATION_REPORT.md')

    with open(report_path, 'w') as f:
        f.write(report)

    print(f"✓ {report_path}")

    # Final summary
    print("\n" + "=" * 80)
    print("✓ COMPLETE")
    print("=" * 80)
    print(f"\nGenerated {len(packs)} conformance pack(s) in {args.output_dir}/")
    print(f"See {report_path} for details")
    print("\nNext steps:")
    print("1. Review the generated conformance pack YAML files")
    print("2. Adjust any parameters as needed for your environment")
    print("3. Deploy using the AWS CLI commands in the report")
    print()


if __name__ == '__main__':
    main()
