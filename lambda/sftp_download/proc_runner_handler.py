"""
Lambda Handlers for Stored Procedure Execution

These handlers provide API endpoints to execute and monitor
the HCM_MAIN_INTF stored procedure.
"""

import json
import os
import boto3
from datetime import datetime

try:
    from .stored_procedure_runner import (
        execute_hcm_main_intf,
        get_procedure_status,
        generate_error_report
    )
except ImportError:
    from sftp_download.stored_procedure_runner import (
        execute_hcm_main_intf,
        get_procedure_status,
        generate_error_report
    )


def make_response(status_code: int, body: dict) -> dict:
    """Create a standardized API Gateway response."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
        },
        'body': json.dumps(body, default=str)
    }


def run_stored_procedure_handler(event, context):
    """
    Lambda handler to execute the HCM_MAIN_INTF stored procedure.

    POST /run-procedure
    Body: {
        "test_mode": true/false  (optional, defaults to true for safety)
        "environment": "test" or "production" (optional, defaults to "test")
    }

    Returns:
        Execution results including status, steps completed, delta counts, and any errors
    """
    try:
        # Parse request body
        if 'body' in event and event['body']:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = {}

        # Get test mode from request (default to True for safety)
        test_mode = body.get('test_mode', True)

        # Get environment from request (default to 'test' for safety)
        environment = body.get('environment', 'test')

        # Determine database based on environment
        # Both databases are on the same server, just different database names
        database_override = None
        if environment == 'production':
            database_override = 'Hacienda ERP'  # Production database
        # else: use default from secret ('Hacienda ERP Test')

        # Get secret name from environment or use default
        secret_name = os.environ.get('SQL_SECRET_NAME', 'Hacienda_ERP_MSSQL_Production')

        # Execute the stored procedure
        result = execute_hcm_main_intf(
            test_execution=test_mode,
            secret_name=secret_name,
            database_override=database_override
        )
        result['environment'] = environment

        # If there was an error, also generate the report and upload to S3
        if result.get('status') == 'error' and result.get('error'):
            try:
                report_content = generate_error_report(result)
                report_key = f"reports/proc_error_{result['execution_id']}.txt"

                s3_client = boto3.client('s3')
                bucket = os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads')

                s3_client.put_object(
                    Bucket=bucket,
                    Key=report_key,
                    Body=report_content.encode('utf-8'),
                    ContentType='text/plain'
                )

                # Generate presigned URL for download
                presigned_url = s3_client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': bucket, 'Key': report_key},
                    ExpiresIn=3600  # 1 hour
                )
                result['report_url'] = presigned_url
                result['report_key'] = report_key

            except Exception as report_error:
                result['report_error'] = str(report_error)

        return make_response(200, result)

    except Exception as e:
        return make_response(500, {
            'status': 'error',
            'error': {
                'message': str(e),
                'type': 'handler_error'
            }
        })


def get_procedure_status_handler(event, context):
    """
    Lambda handler to get the current status of stored procedure execution.

    GET /procedure-status?environment=test|production

    This endpoint can be polled to check on a long-running execution.

    Returns:
        Current run status, completed steps, and delta counts
    """
    try:
        # Get environment from query string (default to 'test' for safety)
        query_params = event.get('queryStringParameters') or {}
        environment = query_params.get('environment', 'test')

        # Determine database based on environment
        database_override = None
        if environment == 'production':
            database_override = 'Hacienda ERP'  # Production database

        # Get secret name from environment or use default
        secret_name = os.environ.get('SQL_SECRET_NAME', 'Hacienda_ERP_MSSQL_Production')

        # Get current status
        result = get_procedure_status(
            secret_name=secret_name,
            database_override=database_override
        )
        result['environment'] = environment

        return make_response(200, result)

    except Exception as e:
        return make_response(500, {
            'error': str(e)
        })


def generate_report_handler(event, context):
    """
    Lambda handler to generate and download an execution report.

    POST /generate-report
    Body: {
        "execution_result": { ... }  (the result from run_stored_procedure_handler)
    }

    Returns:
        Presigned URL to download the report
    """
    try:
        # Parse request body
        if 'body' in event and event['body']:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            return make_response(400, {'error': 'No execution result provided'})

        execution_result = body.get('execution_result', body)

        if not execution_result:
            return make_response(400, {'error': 'No execution result provided'})

        # Generate report content
        report_content = generate_error_report(execution_result)

        # Upload to S3
        s3_client = boto3.client('s3')
        bucket = os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads')
        execution_id = execution_result.get('execution_id', datetime.now().strftime('%Y%m%d_%H%M%S'))
        report_key = f"reports/proc_report_{execution_id}.txt"

        s3_client.put_object(
            Bucket=bucket,
            Key=report_key,
            Body=report_content.encode('utf-8'),
            ContentType='text/plain'
        )

        # Generate presigned URL
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': report_key},
            ExpiresIn=3600  # 1 hour
        )

        return make_response(200, {
            'report_url': presigned_url,
            'report_key': report_key,
            'bucket': bucket
        })

    except Exception as e:
        return make_response(500, {
            'error': str(e)
        })
