"""
Report Generator Module for Hacienda SFTP Downloads

Generates Excel/CSV reports for validation errors.
"""

import boto3
import io
import csv
import json
from datetime import datetime
from typing import Dict, List, Optional
import uuid


def generate_error_report_csv(
    duplicate_result: Dict,
    validation_result: Dict,
    completeness_result: Dict,
    workflow_id: str = None
) -> bytes:
    """
    Generate a CSV error report combining all validation results.

    Args:
        duplicate_result: Results from duplicate check
        validation_result: Results from file name validation
        completeness_result: Results from completeness check
        workflow_id: Optional workflow identifier

    Returns:
        CSV file content as bytes
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow(['Hacienda SFTP File Validation Report'])
    writer.writerow([f'Generated: {datetime.utcnow().isoformat()}'])
    if workflow_id:
        writer.writerow([f'Workflow ID: {workflow_id}'])
    writer.writerow([])

    # Summary Section
    writer.writerow(['=== SUMMARY ==='])
    writer.writerow([])

    # Duplicate summary
    dup_moved = duplicate_result.get('total_moved', 0)
    writer.writerow(['Duplicate Check:'])
    writer.writerow(['', 'Exact Duplicates Moved:', duplicate_result.get('exact_duplicates_moved', 0)])
    writer.writerow(['', 'Superseded Files Moved:', duplicate_result.get('superseded_moved', 0)])
    writer.writerow(['', 'Total Files Moved:', dup_moved])
    writer.writerow([])

    # Validation summary
    valid_count = validation_result.get('valid_count', 0)
    invalid_count = validation_result.get('invalid_count', 0)
    writer.writerow(['File Name Validation:'])
    writer.writerow(['', 'Valid Files:', valid_count])
    writer.writerow(['', 'Invalid Files:', invalid_count])
    writer.writerow(['', 'Status:', 'PASSED' if invalid_count == 0 else 'FAILED'])
    writer.writerow([])

    # Completeness summary
    complete_sets = completeness_result.get('complete_sets', 0)
    incomplete_sets = completeness_result.get('incomplete_sets', 0)
    writer.writerow(['Completeness Check:'])
    writer.writerow(['', 'Complete Sets:', complete_sets])
    writer.writerow(['', 'Incomplete Sets:', incomplete_sets])
    writer.writerow(['', 'Status:', 'PASSED' if incomplete_sets == 0 else 'FAILED'])
    writer.writerow([])

    # Overall Status
    has_errors = invalid_count > 0 or incomplete_sets > 0
    writer.writerow(['Overall Status:', 'ERRORS FOUND' if has_errors else 'ALL CHECKS PASSED'])
    writer.writerow([])

    # Detailed Sections
    writer.writerow(['=== DETAILED RESULTS ==='])
    writer.writerow([])

    # Section 1: Files Moved to DuplicateCheck
    writer.writerow(['--- FILES MOVED TO DuplicateCheck FOLDER ---'])
    writer.writerow(['Source File', 'Destination', 'Reason'])

    files_moved = duplicate_result.get('files_moved', [])
    if files_moved:
        for move in files_moved:
            source = move.get('source', '')
            dest = move.get('destination', '')
            # Determine reason
            reason = 'Duplicate or Older Version'
            writer.writerow([source, dest, reason])
    else:
        writer.writerow(['No files were moved'])
    writer.writerow([])

    # Section 2: Invalid File Names
    writer.writerow(['--- INVALID FILE NAMES ---'])
    writer.writerow(['File Name', 'Error', 'Suggested Correction'])

    invalid_files = validation_result.get('invalid_files', [])
    if invalid_files:
        for file_info in invalid_files:
            filename = file_info.get('file_name', file_info.get('file', ''))
            error = file_info.get('error_message', file_info.get('error', ''))
            suggestion = file_info.get('suggested_correction', file_info.get('suggestion', ''))
            writer.writerow([filename, error, suggestion or ''])
    else:
        writer.writerow(['All file names are valid'])
    writer.writerow([])

    # Section 3: Incomplete File Sets
    writer.writerow(['--- INCOMPLETE FILE SETS ---'])
    writer.writerow(['Entity', 'Date', 'Missing Sources', 'Present Sources'])

    file_sets = completeness_result.get('file_sets', [])
    incomplete_found = False
    for fs in file_sets:
        if not fs.get('is_complete', True):
            incomplete_found = True
            entity = fs.get('entity', '')
            date = fs.get('date', '')
            missing = ', '.join(fs.get('missing_sources', []))
            present = ', '.join(fs.get('files', {}).keys()) if isinstance(fs.get('files'), dict) else ''
            writer.writerow([entity, date, missing, present])

    if not incomplete_found:
        writer.writerow(['All file sets are complete'])
    writer.writerow([])

    # Section 4: Files Kept (for SQL Loading)
    writer.writerow(['--- FILES TO LOAD TO SQL SERVER ---'])
    writer.writerow(['File Path'])

    files_kept = duplicate_result.get('files_kept', [])
    if files_kept:
        for f in files_kept:
            writer.writerow([f])
    else:
        writer.writerow(['No files available for loading'])

    return output.getvalue().encode('utf-8')


def upload_report_to_s3(
    report_content: bytes,
    bucket_name: str,
    report_name: str = None,
    s3_client=None
) -> Dict:
    """
    Upload report to S3 and generate a presigned URL.

    Args:
        report_content: Report file content as bytes
        bucket_name: S3 bucket name
        report_name: Optional report filename
        s3_client: Optional boto3 S3 client

    Returns:
        Dictionary with S3 key and presigned URL
    """
    if s3_client is None:
        s3_client = boto3.client('s3')

    if report_name is None:
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        report_name = f'validation_report_{timestamp}.csv'

    s3_key = f'reports/{report_name}'

    # Upload to S3
    s3_client.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=report_content,
        ContentType='text/csv'
    )

    # Generate presigned URL (valid for 1 hour)
    presigned_url = s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket_name, 'Key': s3_key},
        ExpiresIn=3600
    )

    return {
        's3_key': s3_key,
        'presigned_url': presigned_url,
        'expires_in_seconds': 3600
    }


def generate_and_upload_report(
    bucket_name: str,
    duplicate_result: Dict,
    validation_result: Dict,
    completeness_result: Dict,
    workflow_id: str = None,
    s3_client=None
) -> Dict:
    """
    Generate and upload an error report to S3.

    Args:
        bucket_name: S3 bucket name
        duplicate_result: Results from duplicate check
        validation_result: Results from file name validation
        completeness_result: Results from completeness check
        workflow_id: Optional workflow identifier
        s3_client: Optional boto3 S3 client

    Returns:
        Dictionary with report info and download URL
    """
    if workflow_id is None:
        workflow_id = str(uuid.uuid4())[:8]

    # Generate report content
    report_content = generate_error_report_csv(
        duplicate_result,
        validation_result,
        completeness_result,
        workflow_id
    )

    # Upload to S3
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    report_name = f'validation_report_{workflow_id}_{timestamp}.csv'

    upload_result = upload_report_to_s3(
        report_content,
        bucket_name,
        report_name,
        s3_client
    )

    return {
        'workflow_id': workflow_id,
        'report_name': report_name,
        's3_key': upload_result['s3_key'],
        'download_url': upload_result['presigned_url'],
        'expires_in_seconds': upload_result['expires_in_seconds']
    }


# Lambda handler for report generation
def generate_report_handler(event, context):
    """
    Lambda handler for generating validation reports.

    Expected event format:
    {
        "duplicate_result": {...},
        "validation_result": {...},
        "completeness_result": {...},
        "workflow_id": "optional-id"
    }
    """
    import os

    try:
        # Parse request body if from API Gateway
        if 'body' in event and event['body']:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event

        bucket_name = body.get('bucket', os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads'))
        duplicate_result = body.get('duplicate_result', {})
        validation_result = body.get('validation_result', {})
        completeness_result = body.get('completeness_result', {})
        workflow_id = body.get('workflow_id')

        result = generate_and_upload_report(
            bucket_name,
            duplicate_result,
            validation_result,
            completeness_result,
            workflow_id
        )

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(result)
        }

    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': str(e)})
        }


# Lambda handler for getting presigned download URL
def get_report_url_handler(event, context):
    """
    Lambda handler for getting a presigned URL to download a report.

    Expected path parameter: report_id (the S3 key of the report)
    """
    import os

    try:
        # Get report ID from path parameters
        path_params = event.get('pathParameters', {}) or {}
        report_id = path_params.get('report_id', '')

        if not report_id:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({'error': 'report_id is required'})
            }

        bucket_name = os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads')
        s3_key = f'reports/{report_id}'

        s3_client = boto3.client('s3')

        # Check if report exists
        try:
            s3_client.head_object(Bucket=bucket_name, Key=s3_key)
        except s3_client.exceptions.ClientError:
            return {
                'statusCode': 404,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({'error': 'Report not found'})
            }

        # Generate presigned URL
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': s3_key},
            ExpiresIn=3600
        )

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'report_id': report_id,
                'download_url': presigned_url,
                'expires_in_seconds': 3600
            })
        }

    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': str(e)})
        }
