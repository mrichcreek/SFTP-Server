"""
Workflow Handler for Hacienda SFTP Download Application
Extends the base handler with validation, completeness checking, and database loading.

This module orchestrates the complete workflow:
1. Download files from SFTP
2. Check for duplicates
3. Validate file names
4. Check completeness
5. Load to database
6. Execute HCM interface stored procedure
"""

import json
import boto3
import uuid
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional
from decimal import Decimal

# Initialize AWS clients directly (for Lambda deployment)
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# Environment variables
S3_BUCKET = os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads')
JOBS_TABLE = os.environ.get('JOBS_TABLE', 'hacienda-sftp-download-jobs-prod')

# Add the Lambda task root to path for imports
# In Lambda, the code is at /var/task/
lambda_root = os.environ.get('LAMBDA_TASK_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if lambda_root not in sys.path:
    sys.path.insert(0, lambda_root)

# Import validation modules - required for validation features
# Use fully qualified paths that work in Lambda environment
import importlib.util
def import_from_file(module_name, file_path):
    """Import a module from a specific file path."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Determine the base path for imports
if os.environ.get('LAMBDA_TASK_ROOT'):
    base_path = os.environ['LAMBDA_TASK_ROOT']
else:
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Import validation modules using explicit file paths
_validator_module = import_from_file('file_naming_validator',
    os.path.join(base_path, 'file_validation', 'file_naming_validator.py'))
validate_file_list = _validator_module.validate_file_list
validate_file_name = _validator_module.validate_file_name
VALID_SOURCES = _validator_module.VALID_SOURCES
VALID_ENTITIES = _validator_module.VALID_ENTITIES

_completeness_module = import_from_file('completeness_checker',
    os.path.join(base_path, 'file_validation', 'completeness_checker.py'))
check_completeness = _completeness_module.check_completeness
get_missing_files_report = _completeness_module.get_missing_files_report

_duplicate_module = import_from_file('duplicate_detector',
    os.path.join(base_path, 'file_validation', 'duplicate_detector.py'))
find_exact_duplicates_s3 = _duplicate_module.find_exact_duplicates_s3
check_file_exists_in_s3 = _duplicate_module.check_file_exists_in_s3
move_duplicates_and_superseded = _duplicate_module.move_duplicates_and_superseded

# Import report generator
_report_module = import_from_file('report_generator',
    os.path.join(base_path, 'sftp_download', 'report_generator.py'))
generate_and_upload_report = _report_module.generate_and_upload_report

# Try to import from handler for SFTP functions (optional, not needed for validation)
try:
    from sftp_download.handler import (
        get_sftp_credentials, create_sftp_connection,
        list_remote_files, download_file_to_s3
    )
except ImportError:
    # SFTP functions not available - validation features will still work
    get_sftp_credentials = None
    create_sftp_connection = None
    list_remote_files = None
    download_file_to_s3 = None

# Try to import database loader (optional, requires VPC)
try:
    from data_loader.database_loader import load_multiple_files, execute_hcm_main_interface
except ImportError:
    load_multiple_files = None
    execute_hcm_main_interface = None


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def create_workflow_job(job_id: str, workflow_type: str) -> Dict:
    """Create a new workflow job record in DynamoDB."""
    table = dynamodb.Table(JOBS_TABLE)
    now = datetime.utcnow().isoformat()

    item = {
        'jobId': job_id,
        'workflowType': workflow_type,
        'status': 'STARTED',
        'createdAt': now,
        'updatedAt': now,
        'progress': Decimal('0'),
        'message': f'{workflow_type} workflow started',
        'steps': {}
    }

    table.put_item(Item=item)
    return item


def update_workflow_step(job_id: str, step_name: str, step_data: Dict,
                         status: str = None, progress: int = None, message: str = None):
    """Update a specific workflow step in DynamoDB."""
    table = dynamodb.Table(JOBS_TABLE)
    now = datetime.utcnow().isoformat()

    # Get current item to update steps
    response = table.get_item(Key={'jobId': job_id})
    current_steps = response.get('Item', {}).get('steps', {})
    if isinstance(current_steps, str):
        current_steps = json.loads(current_steps)

    current_steps[step_name] = step_data

    update_expression = "SET updatedAt = :updated, steps = :steps"
    expression_values = {
        ':updated': now,
        ':steps': json.dumps(current_steps, default=json_serial)
    }

    if status:
        update_expression += ", #status = :status"
        expression_values[':status'] = status

    if progress is not None:
        update_expression += ", progress = :progress"
        expression_values[':progress'] = Decimal(str(progress))

    if message:
        update_expression += ", message = :message"
        expression_values[':message'] = message

    table.update_item(
        Key={'jobId': job_id},
        UpdateExpression=update_expression,
        ExpressionAttributeNames={'#status': 'status'} if status else {},
        ExpressionAttributeValues=expression_values
    )


def list_s3_files(bucket: str, prefix: str = "") -> List[Dict]:
    """List all files in S3 bucket with given prefix."""
    files = []
    paginator = s3_client.get_paginator('list_objects_v2')

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if not key.endswith('/'):  # Skip directories
                files.append({
                    'filename': key.split('/')[-1],
                    's3_key': key,
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                    'etag': obj['ETag'].strip('"')
                })

    return files


# ============================================================================
# STEP 1: VALIDATE FILE NAMES
# ============================================================================

def validate_files_step(event, context):
    """
    Lambda handler for file name validation.

    Event format:
    {
        "bucket": "bucket-name",
        "prefix": "optional/prefix/",
        "file_names": ["optional", "list", "of", "files"]
    }
    """
    bucket = event.get("bucket", S3_BUCKET)
    prefix = event.get("prefix", "")
    provided_files = event.get("file_names")

    if provided_files:
        file_names = provided_files
    else:
        files = list_s3_files(bucket, prefix)
        file_names = [f['filename'] for f in files]

    result = validate_file_list(file_names)

    # Build response
    results_json = []
    for r in result["results"]:
        results_json.append({
            "is_valid": r.is_valid,
            "file_name": r.file_name,
            "entity": r.entity,
            "source": r.source,
            "date": r.date_str,
            "error_message": r.error_message,
            "suggested_correction": r.suggested_correction
        })

    invalid_files = [r for r in results_json if not r["is_valid"]]
    correctable_files = [r for r in invalid_files if r["suggested_correction"]]

    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps({
            "total_files": result["total_files"],
            "valid_count": result["valid_count"],
            "invalid_count": result["invalid_count"],
            "correctable_count": len(correctable_files),
            "invalid_files": invalid_files,
            "correctable_files": correctable_files,
            "valid_sources": result["valid_sources"],
            "valid_entities": result["valid_entities"],
            "all_results": results_json
        })
    }


# ============================================================================
# STEP 2: CHECK COMPLETENESS
# ============================================================================

def check_completeness_step(event, context):
    """
    Lambda handler for completeness checking.

    Event format:
    {
        "bucket": "bucket-name",
        "prefix": "optional/prefix/",
        "include_report": false
    }
    """
    bucket = event.get("bucket", S3_BUCKET)
    prefix = event.get("prefix", "")
    include_report = event.get("include_report", False)

    files = list_s3_files(bucket, prefix)
    file_names = [f['filename'] for f in files]

    result = check_completeness(file_names)

    # Build response
    file_sets_json = []
    for fs in result.file_sets:
        file_sets_json.append({
            "entity": fs.entity,
            "date": fs.date,
            "files": fs.files,
            "missing_sources": fs.missing_sources,
            "is_complete": fs.is_complete
        })

    response_body = {
        "total_files": result.total_files,
        "entities_found": result.total_entities_found,
        "complete_sets": result.complete_sets,
        "incomplete_sets": result.incomplete_sets,
        "completeness_percentage": result.summary.get("complete_percentage", 0),
        "file_sets": file_sets_json,
        "orphan_files": result.orphan_files,
        "duplicate_files": result.duplicate_files,
        "summary": result.summary
    }

    if include_report:
        response_body["report"] = get_missing_files_report(result)

    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(response_body)
    }


# ============================================================================
# STEP 3: CHECK DUPLICATES
# ============================================================================

def check_duplicates_step(event, context):
    """
    Lambda handler for duplicate detection.

    Event format:
    {
        "bucket": "bucket-name",
        "prefix": "optional/prefix/"
    }

    Returns:
    - exact_duplicate_groups: Files with identical content (same hash)
    - superseded_groups: Files of same type but different dates (keep newest)
    """
    bucket = event.get("bucket", S3_BUCKET)
    prefix = event.get("prefix", "")

    result = find_exact_duplicates_s3(bucket, prefix, s3_client, include_superseded=True)

    # Exact duplicates (same content/hash)
    exact_groups_json = []
    for group in result.groups:
        exact_groups_json.append({
            "key": group.key,
            "files": group.files,
            "recommended_keep": group.recommended_keep,
            "type": "exact_duplicate"
        })

    # Superseded files (same type, older dates)
    superseded_json = []
    if result.superseded_groups:
        for group in result.superseded_groups:
            # Clean up internal fields before returning
            clean_files = []
            for f in group.files:
                clean_file = {k: v for k, v in f.items() if not k.startswith('_')}
                clean_file['date'] = f.get('_date_portion', '')
                clean_files.append(clean_file)

            superseded_json.append({
                "file_type": group.file_type,
                "entity": group.entity,
                "files": clean_files,
                "recommended_keep": group.recommended_keep,
                "superseded_files": group.superseded_files,
                "type": "superseded"
            })

    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps({
            "total_files": result.total_files,
            "unique_files": result.unique_files,
            "exact_duplicate_groups": len(exact_groups_json),
            "total_exact_duplicates": result.total_duplicates,
            "superseded_groups_count": len(superseded_json),
            "total_superseded": result.total_superseded,
            "storage_waste_bytes": result.storage_waste_bytes,
            "storage_waste_mb": round(result.storage_waste_bytes / 1024 / 1024, 2),
            "exact_duplicates": exact_groups_json,
            "superseded": superseded_json
        })
    }


# ============================================================================
# STEP 4: LOAD TO DATABASE
# ============================================================================

def load_database_step(event, context):
    """
    Lambda handler for database loading.

    Event format:
    {
        "bucket": "bucket-name",
        "prefix": "optional/prefix/",
        "s3_keys": ["optional", "list", "of", "keys"]
    }
    """
    bucket = event.get("bucket", S3_BUCKET)
    prefix = event.get("prefix", "")
    provided_keys = event.get("s3_keys")

    if provided_keys:
        s3_keys = provided_keys
    else:
        files = list_s3_files(bucket, prefix)
        s3_keys = [f['s3_key'] for f in files if f['filename'].lower().endswith('.csv')]

    if not s3_keys:
        return {
            'statusCode': 400,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({"error": "No CSV files to load"})
        }

    result = load_multiple_files(bucket, s3_keys)

    return {
        'statusCode': 200 if result["failure_count"] == 0 else 207,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(result)
    }


# ============================================================================
# STEP 5: EXECUTE HCM INTERFACE
# ============================================================================

def execute_interface_step(event, context):
    """
    Lambda handler for executing HCM interface stored procedure.

    Event format:
    {
        "test_mode": false
    }
    """
    test_mode = event.get("test_mode", False)
    result = execute_hcm_main_interface(test_mode=test_mode)

    return {
        'statusCode': 200 if result["success"] else 500,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(result)
    }


# ============================================================================
# COMPLETE WORKFLOW ORCHESTRATOR
# ============================================================================

def run_complete_workflow(event, context):
    """
    Lambda handler that orchestrates the complete workflow.

    Event format:
    {
        "bucket": "bucket-name",
        "prefix": "optional/prefix/",
        "options": {
            "skip_validation": false,
            "skip_completeness_check": false,
            "skip_database_load": false,
            "skip_interface_execution": false,
            "continue_on_validation_errors": false,
            "continue_on_incomplete_files": true
        }
    }
    """
    job_id = str(uuid.uuid4())
    bucket = event.get("bucket", S3_BUCKET)
    prefix = event.get("prefix", "")
    options = event.get("options", {})

    # Create job record
    create_workflow_job(job_id, "COMPLETE_VALIDATION_WORKFLOW")

    workflow_result = {
        "job_id": job_id,
        "bucket": bucket,
        "prefix": prefix,
        "steps": {},
        "success": True
    }

    try:
        # ========================
        # STEP 1: List Files
        # ========================
        update_workflow_step(job_id, "list_files", {"status": "running"},
                            status="IN_PROGRESS", progress=5, message="Listing files in S3...")

        files = list_s3_files(bucket, prefix)
        file_names = [f['filename'] for f in files]
        csv_files = [f for f in files if f['filename'].lower().endswith('.csv')]

        workflow_result["steps"]["list_files"] = {
            "status": "completed",
            "total_files": len(files),
            "csv_files": len(csv_files)
        }
        update_workflow_step(job_id, "list_files", workflow_result["steps"]["list_files"],
                            progress=10, message=f"Found {len(files)} files ({len(csv_files)} CSV)")

        if not files:
            workflow_result["success"] = False
            workflow_result["error"] = "No files found"
            update_workflow_step(job_id, "list_files",
                               {"status": "failed", "error": "No files found"},
                               status="FAILED", message="No files found in S3")
            return format_response(400, workflow_result)

        # ========================
        # STEP 2: Check Duplicates
        # ========================
        update_workflow_step(job_id, "duplicates", {"status": "running"},
                            progress=15, message="Checking for duplicate files...")

        dup_result = find_exact_duplicates_s3(bucket, prefix, s3_client)

        workflow_result["steps"]["duplicates"] = {
            "status": "completed",
            "total_duplicates": dup_result.total_duplicates,
            "unique_files": dup_result.unique_files,
            "storage_waste_mb": round(dup_result.storage_waste_bytes / 1024 / 1024, 2)
        }
        update_workflow_step(job_id, "duplicates", workflow_result["steps"]["duplicates"],
                            progress=25, message=f"Found {dup_result.total_duplicates} duplicates")

        # ========================
        # STEP 3: Validate Names
        # ========================
        if not options.get("skip_validation", False):
            update_workflow_step(job_id, "validation", {"status": "running"},
                                progress=30, message="Validating file names...")

            val_result = validate_file_list(file_names)

            invalid_files = [
                {"file": r.file_name, "error": r.error_message, "suggestion": r.suggested_correction}
                for r in val_result["results"] if not r.is_valid
            ]

            workflow_result["steps"]["validation"] = {
                "status": "completed" if val_result["invalid_count"] == 0 else "warning",
                "valid_count": val_result["valid_count"],
                "invalid_count": val_result["invalid_count"],
                "correctable_count": val_result["correctable_count"],
                "invalid_files": invalid_files[:10]  # Limit to first 10 for response size
            }

            if val_result["invalid_count"] > 0 and not options.get("continue_on_validation_errors", False):
                workflow_result["steps"]["validation"]["status"] = "failed"
                workflow_result["success"] = False
                workflow_result["error"] = f"{val_result['invalid_count']} files have invalid names"
                update_workflow_step(job_id, "validation", workflow_result["steps"]["validation"],
                                    status="VALIDATION_FAILED", progress=35,
                                    message=f"Validation failed: {val_result['invalid_count']} invalid files")
                return format_response(400, workflow_result)

            update_workflow_step(job_id, "validation", workflow_result["steps"]["validation"],
                                progress=45, message=f"Validation: {val_result['valid_count']} valid, {val_result['invalid_count']} invalid")
        else:
            workflow_result["steps"]["validation"] = {"status": "skipped"}

        # ========================
        # STEP 4: Check Completeness
        # ========================
        if not options.get("skip_completeness_check", False):
            update_workflow_step(job_id, "completeness", {"status": "running"},
                                progress=50, message="Checking file completeness...")

            comp_result = check_completeness(file_names)

            incomplete_sets = [
                {"entity": fs.entity, "date": fs.date, "missing": fs.missing_sources}
                for fs in comp_result.file_sets if not fs.is_complete
            ]

            workflow_result["steps"]["completeness"] = {
                "status": "completed" if comp_result.incomplete_sets == 0 else "warning",
                "complete_sets": comp_result.complete_sets,
                "incomplete_sets": comp_result.incomplete_sets,
                "completeness_percentage": round(comp_result.summary.get("complete_percentage", 0), 1),
                "entities_found": list(comp_result.summary.get("entities_found", [])),
                "incomplete_details": incomplete_sets[:10]  # Limit for response size
            }

            if comp_result.incomplete_sets > 0 and not options.get("continue_on_incomplete_files", True):
                workflow_result["steps"]["completeness"]["status"] = "failed"
                workflow_result["success"] = False
                workflow_result["error"] = f"{comp_result.incomplete_sets} incomplete file sets"
                update_workflow_step(job_id, "completeness", workflow_result["steps"]["completeness"],
                                    status="INCOMPLETE_FILES", progress=55,
                                    message=f"Incomplete: {comp_result.incomplete_sets} sets missing files")
                return format_response(400, workflow_result)

            update_workflow_step(job_id, "completeness", workflow_result["steps"]["completeness"],
                                progress=65, message=f"Completeness: {comp_result.complete_sets} complete, {comp_result.incomplete_sets} incomplete")
        else:
            workflow_result["steps"]["completeness"] = {"status": "skipped"}

        # ========================
        # STEP 5: Load to Database
        # ========================
        if not options.get("skip_database_load", False):
            update_workflow_step(job_id, "database_load", {"status": "running"},
                                progress=70, message="Loading files to database...")

            s3_keys = [f['s3_key'] for f in csv_files]
            load_result = load_multiple_files(bucket, s3_keys)

            workflow_result["steps"]["database_load"] = {
                "status": "completed" if load_result["failure_count"] == 0 else "partial",
                "files_loaded": load_result["success_count"],
                "files_failed": load_result["failure_count"],
                "total_rows": load_result["total_rows_loaded"]
            }

            if load_result["failure_count"] > 0:
                workflow_result["steps"]["database_load"]["failed_files"] = [
                    {"file": r["file_name"], "error": r["error_message"]}
                    for r in load_result["results"] if not r["success"]
                ][:10]

            update_workflow_step(job_id, "database_load", workflow_result["steps"]["database_load"],
                                progress=85, message=f"Loaded {load_result['success_count']} files, {load_result['total_rows_loaded']} rows")
        else:
            workflow_result["steps"]["database_load"] = {"status": "skipped"}

        # ========================
        # STEP 6: Execute Interface
        # ========================
        if not options.get("skip_interface_execution", False) and not options.get("skip_database_load", False):
            update_workflow_step(job_id, "hcm_interface", {"status": "running"},
                                progress=90, message="Executing HCM interface...")

            interface_result = execute_hcm_main_interface()

            workflow_result["steps"]["hcm_interface"] = {
                "status": "completed" if interface_result["success"] else "failed",
                "message": interface_result.get("message") or interface_result.get("error")
            }

            if not interface_result["success"]:
                workflow_result["steps"]["hcm_interface"]["error"] = interface_result.get("error")

            update_workflow_step(job_id, "hcm_interface", workflow_result["steps"]["hcm_interface"],
                                progress=95, message="HCM interface executed")
        else:
            workflow_result["steps"]["hcm_interface"] = {"status": "skipped"}

        # ========================
        # COMPLETE
        # ========================
        final_status = "COMPLETED" if workflow_result["success"] else "COMPLETED_WITH_WARNINGS"
        update_workflow_step(job_id, "complete", {"status": "done"},
                            status=final_status, progress=100,
                            message="Workflow completed successfully")

        return format_response(200, workflow_result)

    except Exception as e:
        workflow_result["success"] = False
        workflow_result["error"] = str(e)
        update_workflow_step(job_id, "error", {"error": str(e)},
                            status="FAILED", message=f"Workflow failed: {str(e)}")
        return format_response(500, workflow_result)


def format_response(status_code: int, body: Dict) -> Dict:
    """Format Lambda response with proper headers."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
        },
        'body': json.dumps(body, default=json_serial)
    }


# ============================================================================
# GET WORKFLOW STATUS
# ============================================================================

def get_workflow_status(event, context):
    """
    Lambda handler to get workflow job status.

    Event format:
    {
        "job_id": "uuid"
    }
    Or from path parameters:
    event['pathParameters']['jobId']
    """
    job_id = event.get("job_id")
    if not job_id and event.get('pathParameters'):
        job_id = event['pathParameters'].get('jobId')

    if not job_id:
        return format_response(400, {"error": "job_id is required"})

    table = dynamodb.Table(JOBS_TABLE)
    response = table.get_item(Key={'jobId': job_id})

    if 'Item' not in response:
        return format_response(404, {"error": f"Job not found: {job_id}"})

    item = response['Item']

    # Parse steps JSON if it's a string
    if 'steps' in item and isinstance(item['steps'], str):
        try:
            item['steps'] = json.loads(item['steps'])
        except:
            pass

    # Convert Decimal to float
    if 'progress' in item:
        item['progress'] = float(item['progress'])

    return format_response(200, item)


# ============================================================================
# INTEGRATED VALIDATION WORKFLOW (with auto-move and reporting)
# ============================================================================

def run_validation_workflow_handler(event, context):
    """
    Lambda handler for the integrated validation workflow.

    This workflow:
    1. Checks for duplicates and superseded files -> auto-moves them to DuplicateCheck/
    2. Validates file names
    3. Checks completeness (each source has all 8 entity types)
    4. If errors in 2 or 3: generates error report and returns URL
    5. If no errors: proceeds to SQL load (if enabled)

    Event format:
    {
        "bucket": "bucket-name",
        "prefix": "optional/prefix/",
        "load_to_sql": false  // Set true to load to SQL if all checks pass
    }

    Returns workflow result with step details and report URL if errors found.
    """
    parsed_event = parse_api_event(event) if 'body' in event else event

    job_id = str(uuid.uuid4())[:8]
    bucket = parsed_event.get("bucket", S3_BUCKET)
    prefix = parsed_event.get("prefix", "")
    load_to_sql = parsed_event.get("load_to_sql", False)

    workflow_result = {
        "workflow_id": job_id,
        "status": "in_progress",
        "bucket": bucket,
        "prefix": prefix,
        "steps": {},
        "has_errors": False,
        "report_url": None
    }

    try:
        # ========================
        # STEP 1: List Initial Files
        # ========================
        initial_files = list_s3_files(bucket, prefix)
        # Exclude files already in DuplicateCheck folder
        initial_files = [f for f in initial_files if not f['s3_key'].startswith('DuplicateCheck/')]

        if not initial_files:
            workflow_result["status"] = "failed"
            workflow_result["error"] = "No files found in S3 bucket"
            return format_response(400, workflow_result)

        workflow_result["steps"]["initial_files"] = {
            "status": "completed",
            "total_files": len(initial_files)
        }

        # ========================
        # STEP 2: Check Duplicates and Move to DuplicateCheck/
        # ========================
        dup_result = find_exact_duplicates_s3(bucket, prefix, s3_client, include_superseded=True)

        # Auto-move duplicates and superseded files
        move_result = move_duplicates_and_superseded(
            bucket,
            dup_result,
            destination_folder="DuplicateCheck",
            s3_client=s3_client
        )

        workflow_result["steps"]["duplicates"] = {
            "status": "completed",
            "exact_duplicates_moved": move_result.get('exact_duplicates_moved', 0),
            "superseded_moved": move_result.get('superseded_moved', 0),
            "total_moved": move_result.get('total_moved', 0),
            "files_remaining": len(move_result.get('files_kept', [])),
            "move_errors": move_result.get('errors', [])
        }

        # Get remaining files after move
        remaining_files = list_s3_files(bucket, prefix)
        remaining_files = [f for f in remaining_files if not f['s3_key'].startswith('DuplicateCheck/')]
        file_names = [f['filename'] for f in remaining_files]

        # ========================
        # STEP 3: Validate File Names
        # ========================
        val_result = validate_file_list(file_names)

        invalid_files = [
            {
                "file_name": r.file_name,
                "error_message": r.error_message,
                "suggested_correction": r.suggested_correction
            }
            for r in val_result["results"] if not r.is_valid
        ]

        workflow_result["steps"]["validation"] = {
            "status": "passed" if val_result["invalid_count"] == 0 else "failed",
            "valid_count": val_result["valid_count"],
            "invalid_count": val_result["invalid_count"],
            "invalid_files": invalid_files
        }

        if val_result["invalid_count"] > 0:
            workflow_result["has_errors"] = True

        # ========================
        # STEP 4: Check Completeness
        # ========================
        comp_result = check_completeness(file_names)

        file_sets_json = []
        for fs in comp_result.file_sets:
            file_sets_json.append({
                "entity": fs.entity,
                "date": fs.date,
                "files": fs.files,
                "missing_sources": fs.missing_sources,
                "is_complete": fs.is_complete
            })

        workflow_result["steps"]["completeness"] = {
            "status": "passed" if comp_result.incomplete_sets == 0 else "failed",
            "complete_sets": comp_result.complete_sets,
            "incomplete_sets": comp_result.incomplete_sets,
            "completeness_percentage": round(comp_result.summary.get("complete_percentage", 0), 1),
            "file_sets": file_sets_json
        }

        if comp_result.incomplete_sets > 0:
            workflow_result["has_errors"] = True

        # ========================
        # STEP 5: Generate Report if Errors
        # ========================
        if workflow_result["has_errors"]:
            # Prepare data for report
            duplicate_data = {
                "total_moved": move_result.get('total_moved', 0),
                "exact_duplicates_moved": move_result.get('exact_duplicates_moved', 0),
                "superseded_moved": move_result.get('superseded_moved', 0),
                "files_moved": move_result.get('files_moved', []),
                "files_kept": move_result.get('files_kept', [])
            }

            validation_data = {
                "valid_count": val_result["valid_count"],
                "invalid_count": val_result["invalid_count"],
                "invalid_files": invalid_files
            }

            completeness_data = {
                "complete_sets": comp_result.complete_sets,
                "incomplete_sets": comp_result.incomplete_sets,
                "file_sets": file_sets_json
            }

            report_result = generate_and_upload_report(
                bucket,
                duplicate_data,
                validation_data,
                completeness_data,
                job_id,
                s3_client
            )

            workflow_result["report_url"] = report_result.get("download_url")
            workflow_result["report_name"] = report_result.get("report_name")
            workflow_result["status"] = "errors_found"

            workflow_result["steps"]["report"] = {
                "status": "generated",
                "report_name": report_result.get("report_name"),
                "download_url": report_result.get("download_url")
            }

        # ========================
        # STEP 6: Load to SQL (only if no errors and requested)
        # ========================
        elif load_to_sql:
            # Import SQL loader
            try:
                _sql_module = import_from_file('sql_table_loader',
                    os.path.join(base_path, 'sftp_download', 'sql_table_loader.py'))
                load_all_newest_files = _sql_module.load_all_newest_files

                # Prepare file list for SQL loader
                files_for_sql = [
                    {
                        "filename": f['filename'],
                        "s3_key": f['s3_key']
                    }
                    for f in remaining_files
                ]

                sql_result = load_all_newest_files(
                    bucket=bucket,
                    files=files_for_sql,
                    drop_existing=True
                )

                workflow_result["steps"]["sql_load"] = {
                    "status": "completed" if sql_result.get("failed", 0) == 0 else "partial",
                    "total_tables": sql_result.get("total_tables", 0),
                    "successful": sql_result.get("successful", 0),
                    "failed": sql_result.get("failed", 0),
                    "tables": sql_result.get("tables", [])
                }

                workflow_result["status"] = "completed"

            except Exception as sql_error:
                workflow_result["steps"]["sql_load"] = {
                    "status": "failed",
                    "error": str(sql_error)
                }
                workflow_result["status"] = "sql_load_failed"
        else:
            workflow_result["status"] = "completed"
            workflow_result["steps"]["sql_load"] = {
                "status": "skipped",
                "message": "SQL load not requested"
            }

        return format_response(200, workflow_result)

    except Exception as e:
        workflow_result["status"] = "failed"
        workflow_result["error"] = str(e)
        return format_response(500, workflow_result)


# ============================================================================
# API GATEWAY HANDLERS (parse body from API Gateway events)
# ============================================================================

def parse_api_event(event):
    """Parse body from API Gateway event."""
    if event.get('body'):
        try:
            return json.loads(event['body'])
        except (json.JSONDecodeError, TypeError):
            return {}
    return event


def validate_files_handler(event, context):
    """API Gateway handler for file validation."""
    parsed_event = parse_api_event(event)
    return validate_files_step(parsed_event, context)


def check_completeness_handler(event, context):
    """API Gateway handler for completeness checking."""
    parsed_event = parse_api_event(event)
    return check_completeness_step(parsed_event, context)


def check_duplicates_handler(event, context):
    """API Gateway handler for duplicate detection."""
    parsed_event = parse_api_event(event)
    return check_duplicates_step(parsed_event, context)


def list_files_handler(event, context):
    """API Gateway handler for listing files in S3."""
    parsed_event = parse_api_event(event)
    bucket = parsed_event.get("bucket", S3_BUCKET)
    prefix = parsed_event.get("prefix", "")

    try:
        files = list_s3_files(bucket, prefix)
        return format_response(200, {
            "total_files": len(files),
            "files": files
        })
    except Exception as e:
        return format_response(500, {"error": str(e)})


def run_workflow_handler(event, context):
    """API Gateway handler for integrated validation workflow."""
    return run_validation_workflow_handler(event, context)


# ============================================================================
# MAIN ROUTER
# ============================================================================

def workflow_handler(event, context):
    """
    Main Lambda handler that routes to specific workflow handlers.

    Event format:
    {
        "handler": "validate" | "completeness" | "duplicates" | "load" | "interface" | "workflow" | "status",
        ...handler-specific params...
    }
    """
    handler_name = event.get("handler", "workflow")

    handlers = {
        "validate": validate_files_step,
        "completeness": check_completeness_step,
        "duplicates": check_duplicates_step,
        "load": load_database_step,
        "interface": execute_interface_step,
        "workflow": run_complete_workflow,
        "status": get_workflow_status
    }

    handler_func = handlers.get(handler_name)
    if not handler_func:
        return format_response(400, {
            "error": f"Unknown handler: {handler_name}",
            "valid_handlers": list(handlers.keys())
        })

    return handler_func(event, context)
