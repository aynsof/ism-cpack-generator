"""
AWS Lambda Function: Conformance Pack Aggregator

Step Functions Task 3: Aggregate batch results and generate conformance packs
- Combine all processed rules from batches
- Split rules into conformance packs (respecting 50KB size limit)
- Generate YAML files
- Upload to S3

Event Format:
{
    "job_id": "uuid",
    "prefix": "ism-controls",
    "batch_results": [
        {
            "batch_id": 1,
            "processed_rules": [...],
            "errors": [...],
            "rules_processed": 50,
            "rules_failed": 0
        },
        ...
    ]
}

Returns:
{
    "statusCode": 200,
    "message": "Conformance packs generated successfully",
    "job_id": "uuid",
    "packs_generated": 3,
    "total_rules": 870,
    "failed_rules": 10,
    "files": [
        {
            "pack_name": "ism-controls-01",
            "file_name": "conformance-pack-ism-controls-01.yaml",
            "s3_key": "...",
            "s3_url": "...",
            "rules_count": 130,
            "size_bytes": 45000
        },
        ...
    ],
    "report_url": "s3://bucket/path/to/report.md"
}

Environment Variables:
- OUTPUT_BUCKET_NAME: S3 bucket for conformance pack YAML files
"""

import boto3
import json
import yaml
import os
from typing import List, Dict, Any
from datetime import datetime

# Initialize AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# Configuration from environment
OUTPUT_BUCKET = os.environ['OUTPUT_BUCKET_NAME']
CONFIG_MAPPINGS_TABLE_NAME = os.environ.get('CONFIG_MAPPINGS_TABLE_NAME', '')

# Constants
MAX_PACK_SIZE_BYTES = 51200  # 50 KB
MAX_RULES_PER_PACK = 130


def to_pascal_case(text: str) -> str:
    """Convert text to PascalCase"""
    if '-' not in text and '_' not in text and text and text[0].islower():
        return text[0].upper() + text[1:]
    return ''.join(word.capitalize() for word in text.replace('_', '-').split('-'))


def create_conformance_pack(rules: List[Dict[str, Any]], pack_name: str) -> str:
    """
    Create a conformance pack YAML from a list of rule configs
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

        resource_name = to_pascal_case(rule_name)

        rule_properties = {
            "ConfigRuleName": rule_name,
            "Description": description,
            "Source": source
        }

        # Handle input parameters
        input_params = rule_config.get("InputParameters", {})
        if input_params:
            filtered_params = {k: v for k, v in input_params.items() if v is not None}

            if filtered_params:
                input_params_yaml = {}

                for param_name, param_value in filtered_params.items():
                    param_key = f"{resource_name}Param{to_pascal_case(param_name)}"
                    condition_key = param_key[0].lower() + param_key[1:] if param_key else param_key

                    parameters[param_key] = {
                        "Default": str(param_value),
                        "Type": "String"
                    }

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

                    input_params_yaml[param_name] = {
                        "Fn::If": [
                            condition_key,
                            {"Ref": param_key},
                            {"Ref": "AWS::NoValue"}
                        ]
                    }

                rule_properties["InputParameters"] = input_params_yaml

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

    # Generate YAML
    yaml_output = yaml.dump(
        conformance_pack,
        default_flow_style=False,
        sort_keys=False,
        width=120,
        indent=2
    )

    # Add header
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
    """Split rules into multiple conformance packs respecting size limits"""
    packs = []
    current_pack = []
    current_size = 0
    pack_number = 1

    for rule_config in rule_configs:
        if "error" in rule_config:
            continue

        rule_name = rule_config.get("ConfigRuleName", "UNKNOWN_RULE")
        description = rule_config.get("Description", "")
        input_params = rule_config.get("InputParameters", {})

        rule_size = len(rule_name) + len(description) + 200

        for param_name, param_value in input_params.items():
            if param_value is not None:
                rule_size += 150 + len(param_name) + len(str(param_value))

        if (len(current_pack) >= MAX_RULES_PER_PACK or
            current_size + rule_size > MAX_PACK_SIZE_BYTES * 0.9):

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


def upload_to_s3(content: str, key: str, content_type: str = 'text/yaml') -> str:
    """Upload content to S3 and return the S3 URL"""
    try:
        s3_client.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=key,
            Body=content.encode('utf-8'),
            ContentType=content_type
        )

        s3_url = f"s3://{OUTPUT_BUCKET}/{key}"
        print(f"✓ Uploaded to {s3_url}")
        return s3_url

    except Exception as e:
        print(f"✗ Error uploading to S3: {e}")
        raise


def generate_mappings_html(job_id: str) -> str:
    """
    Query DynamoDB for all control mappings and generate an HTML report
    """
    try:
        print(f"\nQuerying ConfigMappings table for job_id: {job_id}")

        table = dynamodb.Table(CONFIG_MAPPINGS_TABLE_NAME)

        # Scan table filtering by job_id
        response = table.scan(
            FilterExpression='job_id = :jid',
            ExpressionAttributeValues={':jid': job_id}
        )

        mappings = response.get('Items', [])

        # Handle pagination
        while 'LastEvaluatedKey' in response:
            response = table.scan(
                FilterExpression='job_id = :jid',
                ExpressionAttributeValues={':jid': job_id},
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            mappings.extend(response.get('Items', []))

        print(f"✓ Found {len(mappings)} control mappings")

        # Sort mappings by control_id
        mappings.sort(key=lambda x: x.get('control_id', ''))

        # Generate HTML
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ISM Control Mappings Report - {job_id}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            overflow: hidden;
        }}

        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
        }}

        .header h1 {{
            font-size: 28px;
            margin-bottom: 10px;
        }}

        .header .meta {{
            opacity: 0.9;
            font-size: 14px;
        }}

        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 30px;
            background: #f8f9fa;
            border-bottom: 1px solid #e0e0e0;
        }}

        .stat-card {{
            background: white;
            padding: 20px;
            border-radius: 6px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}

        .stat-card .label {{
            color: #666;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }}

        .stat-card .value {{
            font-size: 24px;
            font-weight: bold;
            color: #667eea;
        }}

        .search-container {{
            padding: 20px 30px;
            background: white;
            border-bottom: 1px solid #e0e0e0;
        }}

        .search-box {{
            width: 100%;
            padding: 12px 20px;
            font-size: 14px;
            border: 2px solid #e0e0e0;
            border-radius: 6px;
            transition: border-color 0.3s;
        }}

        .search-box:focus {{
            outline: none;
            border-color: #667eea;
        }}

        .table-container {{
            overflow-x: auto;
            padding: 30px;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}

        thead {{
            background: #f8f9fa;
            position: sticky;
            top: 0;
        }}

        th {{
            padding: 15px;
            text-align: left;
            font-weight: 600;
            color: #555;
            border-bottom: 2px solid #e0e0e0;
            white-space: nowrap;
        }}

        td {{
            padding: 15px;
            border-bottom: 1px solid #f0f0f0;
            vertical-align: top;
        }}

        tr:hover {{
            background: #f8f9fa;
        }}

        .control-id {{
            font-weight: 600;
            color: #667eea;
            font-family: 'Monaco', 'Courier New', monospace;
            font-size: 13px;
        }}

        .config-rule {{
            font-family: 'Monaco', 'Courier New', monospace;
            font-size: 12px;
            color: #e67e22;
            background: #fff3e0;
            padding: 4px 8px;
            border-radius: 4px;
            display: inline-block;
        }}

        .explanation {{
            color: #555;
            line-height: 1.6;
        }}

        .control-description {{
            color: #666;
            font-size: 13px;
            margin-top: 5px;
        }}

        .footer {{
            padding: 20px 30px;
            background: #f8f9fa;
            text-align: center;
            color: #666;
            font-size: 12px;
            border-top: 1px solid #e0e0e0;
        }}

        .no-results {{
            text-align: center;
            padding: 40px;
            color: #999;
        }}

        @media print {{
            body {{
                background: white;
            }}
            .container {{
                box-shadow: none;
            }}
            .search-container {{
                display: none;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>ISM Control Mappings Report</h1>
            <div class="meta">
                <div>Job ID: {job_id}</div>
                <div>Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</div>
            </div>
        </div>

        <div class="stats">
            <div class="stat-card">
                <div class="label">Total Mappings</div>
                <div class="value">{len(mappings)}</div>
            </div>
            <div class="stat-card">
                <div class="label">Unique Controls</div>
                <div class="value">{len(set(m.get('control_id', '') for m in mappings))}</div>
            </div>
            <div class="stat-card">
                <div class="label">Config Rules</div>
                <div class="value">{len(set(m.get('config_rule_identifier', '') for m in mappings))}</div>
            </div>
        </div>

        <div class="search-container">
            <input type="text" class="search-box" id="searchBox" placeholder="Search by control ID, config rule, or explanation...">
        </div>

        <div class="table-container">
            <table id="mappingsTable">
                <thead>
                    <tr>
                        <th style="width: 15%">Control ID</th>
                        <th style="width: 25%">Config Rule</th>
                        <th style="width: 60%">Relevance Explanation</th>
                    </tr>
                </thead>
                <tbody>
"""

        # Add table rows
        for mapping in mappings:
            control_id = mapping.get('control_id', 'N/A')
            control_description = mapping.get('control_description', '')
            config_rule = mapping.get('config_rule_identifier', 'N/A')
            explanation = mapping.get('relevance_explanation', 'No explanation provided')

            # Escape HTML entities
            control_description = control_description.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            explanation = explanation.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

            html += f"""                    <tr>
                        <td>
                            <div class="control-id">{control_id}</div>
                            <div class="control-description">{control_description[:150]}{'...' if len(control_description) > 150 else ''}</div>
                        </td>
                        <td><span class="config-rule">{config_rule}</span></td>
                        <td class="explanation">{explanation}</td>
                    </tr>
"""

        # Close HTML
        html += """                </tbody>
            </table>
            <div class="no-results" id="noResults" style="display: none;">
                No mappings found matching your search.
            </div>
        </div>

        <div class="footer">
            Generated by ISM Controls Upload System | Powered by AWS Bedrock (Claude Opus 4.5)
        </div>
    </div>

    <script>
        // Search functionality
        const searchBox = document.getElementById('searchBox');
        const table = document.getElementById('mappingsTable');
        const noResults = document.getElementById('noResults');
        const tbody = table.querySelector('tbody');

        searchBox.addEventListener('input', function() {
            const searchTerm = this.value.toLowerCase();
            const rows = tbody.querySelectorAll('tr');
            let visibleCount = 0;

            rows.forEach(row => {
                const text = row.textContent.toLowerCase();
                if (text.includes(searchTerm)) {
                    row.style.display = '';
                    visibleCount++;
                } else {
                    row.style.display = 'none';
                }
            });

            if (visibleCount === 0) {
                table.style.display = 'none';
                noResults.style.display = 'block';
            } else {
                table.style.display = 'table';
                noResults.style.display = 'none';
            }
        });
    </script>
</body>
</html>
"""

        return html

    except Exception as e:
        print(f"✗ Error generating mappings HTML: {e}")
        import traceback
        print(traceback.format_exc())
        # Return a simple error page
        return f"""<!DOCTYPE html>
<html><head><title>Error</title></head>
<body><h1>Error generating mappings report</h1><p>{str(e)}</p></body>
</html>"""


def lambda_handler(event, context):
    """
    Lambda handler for aggregating batch results and generating conformance packs
    """
    try:
        print("=" * 80)
        print("Conformance Pack Aggregator")
        print("=" * 80)

        # Parse event - get from initResult since Map state doesn't return results
        init_result = event.get('initResult', event)
        job_id = init_result['job_id']
        prefix = init_result.get('prefix', 'ism-controls')
        total_batches = init_result.get('total_batches', 0)

        print(f"\nJob ID: {job_id}")
        print(f"Prefix: {prefix}")
        print(f"Total Batches Expected: {total_batches}")

        # Step 1: Discover and download all batch result files from S3
        print("\n" + "-" * 80)
        print("Discovering batch results from S3...")

        batch_results_prefix = f"conformance-packs/{job_id}/batch-results/"

        # List all batch result files
        paginator = s3_client.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(
            Bucket=OUTPUT_BUCKET,
            Prefix=batch_results_prefix
        )

        batch_result_keys = []
        for page in page_iterator:
            for obj in page.get('Contents', []):
                if obj['Key'].endswith('.json'):
                    batch_result_keys.append(obj['Key'])

        print(f"✓ Found {len(batch_result_keys)} batch result files")

        # Step 2: Download and combine all processed rules from batches
        print("\n" + "-" * 80)
        print("Downloading and combining batch results...")

        all_rules = []
        all_errors = []

        for batch_result_key in sorted(batch_result_keys):
            try:
                # Download batch result from S3
                response = s3_client.get_object(
                    Bucket=OUTPUT_BUCKET,
                    Key=batch_result_key
                )
                batch_result = json.loads(response['Body'].read().decode('utf-8'))

                batch_id = batch_result.get('batch_id', 'unknown')
                processed_rules = batch_result.get('processed_rules', [])
                errors = batch_result.get('errors', [])

                all_rules.extend(processed_rules)
                all_errors.extend(errors)

                print(f"  Batch {batch_id}: {len(processed_rules)} rules processed, {len(errors)} errors")

            except Exception as e:
                print(f"  ✗ Error processing {batch_result_key}: {e}")

        total_rules = len(all_rules)
        failed_rules = len(all_errors)

        print(f"\n✓ Combined {total_rules} rules from {len(batch_result_keys)} batches")
        print(f"✓ Total errors: {failed_rules}")

        # Step 2: Split into conformance packs
        print("\n" + "-" * 80)
        print("Splitting rules into conformance packs...")
        packs = split_into_packs(all_rules, prefix)
        print(f"✓ Created {len(packs)} conformance pack(s)")

        # Step 3: Upload conformance pack YAML files to S3
        print("\n" + "-" * 80)
        print("Uploading conformance pack files to S3...")
        uploaded_files = []

        for pack_name, rules in packs:
            pack_file = f"conformance-pack-{pack_name}.yaml"
            s3_key = f"conformance-packs/{job_id}/{pack_file}"

            pack_yaml = create_conformance_pack(rules, pack_name)
            s3_url = upload_to_s3(pack_yaml, s3_key)

            uploaded_files.append({
                'pack_name': pack_name,
                'file_name': pack_file,
                's3_key': s3_key,
                's3_url': s3_url,
                'rules_count': len(rules),
                'size_bytes': len(pack_yaml.encode('utf-8'))
            })

            print(f"  ✓ {pack_file} ({len(rules)} rules, {len(pack_yaml.encode('utf-8')):,} bytes)")

        # Step 4: Generate summary report
        print("\n" + "-" * 80)
        print("Generating summary report...")

        report = f"""# ISM Controls Conformance Pack Generation Report

Generated: {datetime.utcnow().isoformat()}Z
Job ID: {job_id}

## Summary

- Total unique Config Rules: {total_rules + failed_rules}
- Successfully processed: {total_rules}
- Failed to process: {failed_rules}
- Conformance packs generated: {len(packs)}

## Conformance Packs

"""

        for file_info in uploaded_files:
            size_pct = (file_info['size_bytes'] / MAX_PACK_SIZE_BYTES) * 100
            report += f"""### {file_info['pack_name']}
- File: `{file_info['file_name']}`
- S3 Location: `{file_info['s3_url']}`
- Rules: {file_info['rules_count']}
- Size: {file_info['size_bytes']:,} bytes ({size_pct:.1f}% of limit)
- Deploy command:
  ```bash
  aws s3 cp {file_info['s3_url']} ./conformance-pack-{file_info['pack_name']}.yaml
  aws configservice put-conformance-pack \\
    --conformance-pack-name {file_info['pack_name']} \\
    --template-body file://conformance-pack-{file_info['pack_name']}.yaml
  ```

"""

        # Upload report
        report_key = f"conformance-packs/{job_id}/GENERATION_REPORT.md"
        report_url = upload_to_s3(report, report_key)

        # Step 5: Generate and upload HTML mappings report
        print("\n" + "-" * 80)
        print("Generating HTML control mappings report...")

        html_report = generate_mappings_html(job_id)
        html_report_key = f"conformance-packs/{job_id}/control-mappings-report.html"
        html_report_url = upload_to_s3(html_report, html_report_key, content_type='text/html')

        print("\n" + "=" * 80)
        print("✓ AGGREGATION COMPLETE")
        print("=" * 80)
        print(f"Conformance Packs: {len(packs)}")
        print(f"Total Rules: {total_rules}")
        print(f"Report: {report_url}")
        print(f"HTML Mappings Report: {html_report_url}")

        # Return response
        return {
            'statusCode': 200,
            'message': 'Conformance packs generated successfully',
            'job_id': job_id,
            'packs_generated': len(packs),
            'total_rules': total_rules,
            'failed_rules': failed_rules,
            'files': uploaded_files,
            'report_url': report_url,
            'html_mappings_report': {
                's3_key': html_report_key,
                's3_url': html_report_url
            }
        }

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        print(traceback.format_exc())

        return {
            'statusCode': 500,
            'error': str(e),
            'job_id': event.get('job_id', 'unknown')
        }
