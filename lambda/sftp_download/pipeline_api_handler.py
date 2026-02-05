"""
API handlers for Step Functions pipeline management.
Provides endpoints to start pipeline executions and check status.
"""

import json
import os
import boto3
from datetime import datetime
from typing import Dict, Optional


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


def start_pipeline_handler(event, context):
    """
    Start a new pipeline execution via Step Functions.

    POST /start-pipeline
    Body: {
        "environment": "production",  // Always production now
        "test_mode": false            // Whether to filter stored procedure to test SSNs
    }

    Returns:
    {
        "executionId": "pipeline-20240115-103000",
        "executionArn": "arn:aws:states:...",
        "status": "RUNNING",
        "startedAt": "2024-01-15T10:30:00Z"
    }
    """
    try:
        # Parse request body
        if 'body' in event and event['body']:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = {}

        environment = body.get('environment', 'production')
        test_mode = body.get('test_mode', False)

        # Get Step Functions state machine ARN
        state_machine_arn = os.environ.get('STATE_MACHINE_ARN')
        if not state_machine_arn:
            return make_response(500, {
                'error': 'STATE_MACHINE_ARN environment variable not set'
            })

        # Create Step Functions client
        sfn_client = boto3.client('stepfunctions')

        # Generate unique execution name (must be unique per state machine)
        execution_name = f"pipeline-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        # Prepare input for state machine
        pipeline_input = {
            'environment': environment,
            'test_mode': test_mode,
            'test_execution': 'Y' if test_mode else 'N',
            'started_at': datetime.now().isoformat(),
            'execution_name': execution_name,
            'sql_secret': 'Hacienda_ERP_MSSQL_Production',
            'sftp_secret': 'Sterling_SFTP_Direct_Production',
            'database': 'Hacienda_ERP',
            's3_bucket': os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads')
        }

        # Start execution
        response = sfn_client.start_execution(
            stateMachineArn=state_machine_arn,
            name=execution_name,
            input=json.dumps(pipeline_input)
        )

        # Store job info in DynamoDB for quick lookup
        jobs_table = os.environ.get('JOBS_TABLE', 'hacienda-sftp-download-jobs-prod')
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(jobs_table)

        table.put_item(Item={
            'jobId': execution_name,
            'executionArn': response['executionArn'],
            'status': 'RUNNING',
            'startedAt': datetime.now().isoformat(),
            'environment': environment,
            'test_mode': test_mode,
            'ttl': int(datetime.now().timestamp()) + (7 * 24 * 60 * 60)  # 7 days TTL
        })

        return make_response(200, {
            'execution_id': execution_name,  # Field expected by frontend
            'executionId': execution_name,  # Legacy compatibility
            'executionArn': response['executionArn'],
            'status': 'RUNNING',
            'startedAt': response['startDate'].isoformat(),
            'environment': environment,
            'test_mode': test_mode
        })

    except Exception as e:
        return make_response(500, {
            'error': str(e),
            'error_type': type(e).__name__
        })


def get_pipeline_status_handler(event, context):
    """
    Get status of a pipeline execution.

    GET /pipeline-status/{executionId}

    Returns:
    {
        "executionId": "pipeline-20240115-103000",
        "status": "RUNNING" | "SUCCEEDED" | "FAILED" | "TIMED_OUT" | "ABORTED",
        "currentStep": "SqlLoad",
        "completedSteps": 5,
        "totalSteps": 10,
        "progress": 50,
        "startedAt": "2024-01-15T10:30:00Z",
        "stoppedAt": null,
        "output": {...},  // Only if completed
        "error": null     // Only if failed
    }
    """
    try:
        # Get execution ID from path parameters
        execution_id = event.get('pathParameters', {}).get('executionId')
        if not execution_id:
            return make_response(400, {'error': 'executionId required'})

        # Get execution ARN from DynamoDB
        jobs_table = os.environ.get('JOBS_TABLE', 'hacienda-sftp-download-jobs-prod')
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(jobs_table)

        response = table.get_item(Key={'jobId': execution_id})
        job = response.get('Item')

        if not job:
            return make_response(404, {'error': f'Execution {execution_id} not found'})

        # Get current status from Step Functions
        sfn_client = boto3.client('stepfunctions')

        execution = sfn_client.describe_execution(
            executionArn=job['executionArn']
        )

        # Get execution history for progress tracking
        history = sfn_client.get_execution_history(
            executionArn=job['executionArn'],
            maxResults=100,
            reverseOrder=True
        )

        # Determine current step and completed steps from history
        current_step = 'Starting'
        completed_steps = 0
        steps_info = []

        # Map state names to step numbers (matching Step Functions state machine)
        step_order = {
            'SftpDownload': 1,
            'CreateTimestampedFolder': 2,
            'RunValidation': 3,
            'CheckValidationPassed': 3,
            'GenerateValidationReport': 3,
            'SqlLoad': 4,
            'StartStoredProcedure': 5,
            'WaitForProcedure': 5,
            'CheckProcedureStatus': 5,
            'IsProcedureComplete': 5,
            'ExportDeltaFiles': 6,
            'SftpUpload': 7,
            'GenerateFinalReport': 8,
            'PipelineSuccess': 8,
            'ProcedureFailed': 5,
            'PipelineFailed': 0,
            'PipelineFailedValidation': 3
        }

        completed_states = []

        seen_states = set()
        for event_item in history['events']:
            if event_item['type'] == 'TaskStateEntered':
                state_details = event_item.get('stateEnteredEventDetails', {})
                state_name = state_details.get('name', '')
                if state_name:
                    current_step = state_name
                    seen_states.add(state_name)
            elif event_item['type'] == 'TaskStateExited':
                state_details = event_item.get('stateExitedEventDetails', {})
                state_name = state_details.get('name', '')
                if state_name:
                    completed_states.append(state_name)
                    steps_info.append({
                        'step': state_name,
                        'completed_at': event_item['timestamp'].isoformat()
                    })
            elif event_item['type'] == 'WaitStateEntered':
                state_details = event_item.get('stateEnteredEventDetails', {})
                state_name = state_details.get('name', '')
                if state_name:
                    current_step = state_name
            elif event_item['type'] == 'ChoiceStateEntered':
                state_details = event_item.get('stateEnteredEventDetails', {})
                state_name = state_details.get('name', '')
                if state_name:
                    current_step = state_name

        # Calculate completed steps based on seen states
        max_step = 0
        for state_name in seen_states:
            if state_name in step_order:
                max_step = max(max_step, step_order[state_name])
        completed_steps = max_step

        total_steps = 10
        progress = int((completed_steps / total_steps) * 100)

        # Build response (including fields expected by frontend)
        result = {
            'execution_id': execution_id,
            'executionId': execution_id,  # Legacy compatibility
            'executionArn': job['executionArn'],
            'status': execution['status'],
            'current_state': current_step,  # Field expected by frontend
            'currentStep': current_step,  # Legacy compatibility
            'completed_states': list(set(completed_states)),  # Field expected by frontend
            'completedSteps': completed_steps,
            'totalSteps': total_steps,
            'progress': progress,
            'startedAt': execution['startDate'].isoformat(),
            'stoppedAt': execution.get('stopDate').isoformat() if execution.get('stopDate') else None,
            'environment': job.get('environment', 'production'),
            'test_mode': job.get('test_mode', False)
        }

        # Add output if completed
        if execution.get('output'):
            try:
                result['output'] = json.loads(execution['output'])
            except json.JSONDecodeError:
                result['output'] = execution['output']

        # Add error if failed
        if execution['status'] == 'FAILED':
            result['error'] = execution.get('error')
            result['cause'] = execution.get('cause')

        # Add recent step info
        result['recentSteps'] = steps_info[:5] if steps_info else []

        # Update DynamoDB with latest status
        table.update_item(
            Key={'jobId': execution_id},
            UpdateExpression='SET #status = :status, currentStep = :step, progress = :progress',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': execution['status'],
                ':step': current_step,
                ':progress': progress
            }
        )

        return make_response(200, result)

    except Exception as e:
        return make_response(500, {
            'error': str(e),
            'error_type': type(e).__name__
        })


def list_pipeline_executions_handler(event, context):
    """
    List recent pipeline executions.

    GET /pipeline-executions?limit=10

    Returns list of recent executions with status.
    """
    try:
        # Get limit from query string
        query_params = event.get('queryStringParameters') or {}
        limit = int(query_params.get('limit', 10))

        # Scan DynamoDB for recent jobs (sorted by startedAt desc)
        jobs_table = os.environ.get('JOBS_TABLE', 'hacienda-sftp-download-jobs-prod')
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(jobs_table)

        response = table.scan(
            Limit=limit * 2  # Get more to allow for filtering/sorting
        )

        jobs = response.get('Items', [])

        # Sort by startedAt descending
        jobs.sort(key=lambda x: x.get('startedAt', ''), reverse=True)

        # Limit results
        jobs = jobs[:limit]

        return make_response(200, {
            'executions': jobs,
            'count': len(jobs)
        })

    except Exception as e:
        return make_response(500, {
            'error': str(e),
            'error_type': type(e).__name__
        })


def stop_pipeline_handler(event, context):
    """
    Stop a running pipeline execution.

    POST /pipeline-stop/{executionId}

    Returns status of the stop operation.
    """
    try:
        # Get execution ID from path parameters
        execution_id = event.get('pathParameters', {}).get('executionId')
        if not execution_id:
            return make_response(400, {'error': 'executionId required'})

        # Get execution ARN from DynamoDB
        jobs_table = os.environ.get('JOBS_TABLE', 'hacienda-sftp-download-jobs-prod')
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(jobs_table)

        response = table.get_item(Key={'jobId': execution_id})
        job = response.get('Item')

        if not job:
            return make_response(404, {'error': f'Execution {execution_id} not found'})

        # Stop the execution
        sfn_client = boto3.client('stepfunctions')

        sfn_client.stop_execution(
            executionArn=job['executionArn'],
            cause='User requested stop'
        )

        # Update status in DynamoDB
        table.update_item(
            Key={'jobId': execution_id},
            UpdateExpression='SET #status = :status, stoppedAt = :stopped',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'ABORTED',
                ':stopped': datetime.now().isoformat()
            }
        )

        return make_response(200, {
            'executionId': execution_id,
            'status': 'ABORTED',
            'message': 'Pipeline execution stopped'
        })

    except Exception as e:
        return make_response(500, {
            'error': str(e),
            'error_type': type(e).__name__
        })
