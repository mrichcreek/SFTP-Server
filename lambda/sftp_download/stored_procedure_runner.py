"""
Stored Procedure Runner for HCM_MAIN_INTF

This module executes the HCM_MAIN_INTF stored procedure on SQL Server
and monitors its progress through the ProcTrace and RUN_INTF_STATUS tables.
"""

import boto3
from datetime import datetime
from typing import Dict, List, Optional


def get_aws_secret(secret_name: str, region: str = 'us-east-1') -> str:
    """Retrieve a secret from AWS Secrets Manager."""
    client = boto3.client('secretsmanager', region_name=region)
    response = client.get_secret_value(SecretId=secret_name)

    if 'SecretString' in response:
        return response['SecretString']
    else:
        return response['SecretBinary'].decode('utf-8')


def parse_connection_string(conn_str: str) -> Dict:
    """
    Parse ODBC connection string into components for pymssql.
    """
    params = {}
    for part in conn_str.split(';'):
        if '=' in part:
            key, value = part.split('=', 1)
            key = key.strip().upper()
            value = value.strip()
            if key == 'SERVER':
                if ',' in value:
                    server, port = value.split(',')
                    params['server'] = server.strip()
                    params['port'] = int(port.strip())
                else:
                    params['server'] = value
                    params['port'] = 1433
            elif key == 'DATABASE':
                params['database'] = value
            elif key == 'UID':
                params['user'] = value
            elif key == 'PWD':
                params['password'] = value
    return params


def get_proc_trace_log(cursor, limit: int = 100) -> List[Dict]:
    """
    Get recent entries from the ProcTrace table.

    Args:
        cursor: Database cursor
        limit: Maximum number of entries to return

    Returns:
        List of trace entries with step and timestamp
    """
    try:
        cursor.execute(f"""
            SELECT TOP {limit} step,
                   COALESCE(timestamp, GETDATE()) as timestamp
            FROM dbo.ProcTrace
            ORDER BY COALESCE(timestamp, GETDATE()) DESC
        """)
        rows = cursor.fetchall()
        return [{'step': row[0], 'timestamp': str(row[1])} for row in rows]
    except Exception as e:
        # Table might not have timestamp column or might not exist
        try:
            cursor.execute(f"""
                SELECT TOP {limit} step
                FROM dbo.ProcTrace
            """)
            rows = cursor.fetchall()
            return [{'step': row[0], 'timestamp': None} for row in rows]
        except Exception:
            return []


def get_run_status(cursor) -> Optional[Dict]:
    """
    Get the latest execution status from RUN_INTF_STATUS table.

    Returns:
        Dict with Instance, Status, DateStarted, DateCompleted or None
    """
    try:
        cursor.execute("""
            SELECT TOP 1 Instance, Status, DateStarted, DateCompleted
            FROM dbo.RUN_INTF_STATUS
            ORDER BY Instance DESC
        """)
        row = cursor.fetchone()
        if row:
            return {
                'instance': row[0],
                'status': row[1],
                'date_started': str(row[2]) if row[2] else None,
                'date_completed': str(row[3]) if row[3] else None
            }
    except Exception:
        pass
    return None


def get_delta_counts(cursor) -> Dict[str, int]:
    """
    Get record counts from all DELTA tables.

    Returns:
        Dict mapping table name to record count
    """
    delta_tables = [
        'HCM_PERSON_ADDRESS_INTF_DELTA',
        'HCM_PERSON_ASSIGNMENT_INTF_DELTA',
        'HCM_PERSON_NAME_INTF_DELTA',
        'HCM_PERSON_NID_INTF_DELTA',
        'HCM_PERSON_SUPERVISOR_INTF_DELTA',
        'HCM_PERSON_EMAIL_INTF_DELTA',
        'HCM_EXTERNAL_IDENTIFIER_INTF_DELTA',
        'HCM_DEPARTMENT_INTF_DELTA',
        'HCM_JOBS_INTF_DELTA',
        'HCM_LOCATION_INTF_DELTA'
    ]

    counts = {}
    for table in delta_tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM dbo.[{table}]")
            row = cursor.fetchone()
            counts[table] = row[0] if row else 0
        except Exception:
            counts[table] = -1  # Table doesn't exist or error

    return counts


def clear_proc_trace(cursor):
    """Clear the ProcTrace table before a new run."""
    try:
        cursor.execute("DELETE FROM dbo.ProcTrace")
    except Exception:
        pass  # Table might not exist


def execute_hcm_main_intf(
    test_execution: bool = True,
    secret_name: str = 'Hacienda_ERP_MSSQL_Production',
    database_override: str = None
) -> Dict:
    """
    Execute the HCM_MAIN_INTF stored procedure.

    Args:
        test_execution: If True, run in test mode (filters to test SSNs)
        secret_name: AWS Secrets Manager secret containing connection string
        database_override: Optional database name to override the one in connection string
                          Use 'Hacienda ERP' for production, None for test (default)

    Returns:
        Dict with execution results including:
        - status: 'success', 'error', or 'in_progress'
        - execution_id: Timestamp of this run
        - test_mode: Whether test mode was used
        - steps_completed: List of ProcTrace entries
        - delta_counts: Record counts in DELTA tables
        - error: Error details if any
    """
    import pymssql

    test_flag = 'Y' if test_execution else 'N'
    execution_id = datetime.now().strftime('%Y%m%d_%H%M%S')

    result = {
        'status': 'error',
        'execution_id': execution_id,
        'test_mode': test_execution,
        'database': database_override or 'Hacienda ERP Test',
        'started_at': datetime.now().isoformat(),
        'completed_at': None,
        'steps_completed': [],
        'delta_counts': {},
        'run_status': None,
        'error': None
    }

    try:
        # Get connection string from secrets
        connection_string = get_aws_secret(secret_name)
        conn_params = parse_connection_string(connection_string)

        # Override database if specified (for switching between test/production)
        if database_override:
            conn_params['database'] = database_override

        # Connect to SQL Server
        with pymssql.connect(
            server=conn_params['server'],
            port=conn_params.get('port', 1433),
            user=conn_params['user'],
            password=conn_params['password'],
            database=conn_params['database'],
            tds_version='7.3',
            autocommit=True,  # Stored procedures often need autocommit
            timeout=900  # 15 minute timeout
        ) as conn:
            cursor = conn.cursor()

            # Clear ProcTrace for fresh logging
            clear_proc_trace(cursor)

            # Execute the stored procedure
            try:
                cursor.execute(f"EXEC dbo.HCM_MAIN_INTF @test_execution = '{test_flag}'")

                # Consume all result sets (stored procedure may return multiple)
                while cursor.nextset():
                    pass

            except Exception as proc_error:
                # Capture the SQL error
                result['error'] = {
                    'message': str(proc_error),
                    'type': 'stored_procedure_error'
                }

                # Still try to get partial results
                try:
                    result['steps_completed'] = get_proc_trace_log(cursor)
                    result['run_status'] = get_run_status(cursor)
                except Exception:
                    pass

                result['completed_at'] = datetime.now().isoformat()
                return result

            # Get execution results
            result['steps_completed'] = get_proc_trace_log(cursor)
            result['run_status'] = get_run_status(cursor)
            result['delta_counts'] = get_delta_counts(cursor)

            # Check if procedure completed successfully
            run_status = result['run_status']
            if run_status and run_status.get('status') == '02-Completed':
                result['status'] = 'success'
            elif run_status and run_status.get('status') == '01-InProgress':
                result['status'] = 'in_progress'
            else:
                # Check if there are any steps logged - if yes, might have partially succeeded
                if result['steps_completed']:
                    last_step = result['steps_completed'][0]['step'] if result['steps_completed'] else ''
                    if 'finished' in last_step.lower():
                        result['status'] = 'success'
                    else:
                        result['status'] = 'error'
                        result['error'] = {
                            'message': 'Procedure did not complete normally',
                            'last_step': last_step
                        }
                else:
                    result['status'] = 'success'  # Assume success if no errors thrown

            result['completed_at'] = datetime.now().isoformat()

    except Exception as e:
        result['error'] = {
            'message': str(e),
            'type': 'connection_error'
        }
        result['completed_at'] = datetime.now().isoformat()

    return result


def get_procedure_status(
    secret_name: str = 'Hacienda_ERP_MSSQL_Production',
    database_override: str = None
) -> Dict:
    """
    Get the current status of the stored procedure execution.

    This can be called to poll for status during a long-running execution.

    Args:
        secret_name: AWS Secrets Manager secret containing connection string
        database_override: Optional database name to override the one in connection string
                          Use 'Hacienda ERP' for production, None for test (default)

    Returns:
        Dict with run_status, steps_completed, and delta_counts
    """
    import pymssql

    result = {
        'run_status': None,
        'steps_completed': [],
        'delta_counts': {},
        'database': database_override or 'Hacienda ERP Test',
        'error': None
    }

    try:
        connection_string = get_aws_secret(secret_name)
        conn_params = parse_connection_string(connection_string)

        # Override database if specified (for switching between test/production)
        if database_override:
            conn_params['database'] = database_override

        with pymssql.connect(
            server=conn_params['server'],
            port=conn_params.get('port', 1433),
            user=conn_params['user'],
            password=conn_params['password'],
            database=conn_params['database'],
            tds_version='7.3',
            autocommit=True
        ) as conn:
            cursor = conn.cursor()

            result['run_status'] = get_run_status(cursor)
            result['steps_completed'] = get_proc_trace_log(cursor)
            result['delta_counts'] = get_delta_counts(cursor)

    except Exception as e:
        result['error'] = str(e)

    return result


def generate_error_report(execution_result: Dict) -> str:
    """
    Generate a text error report from execution results.

    Args:
        execution_result: Result dict from execute_hcm_main_intf

    Returns:
        Formatted error report string
    """
    lines = []
    lines.append("=" * 80)
    lines.append("HCM_MAIN_INTF EXECUTION REPORT")
    lines.append("=" * 80)
    lines.append("")

    lines.append(f"Execution ID: {execution_result.get('execution_id', 'N/A')}")
    lines.append(f"Test Mode: {'Yes' if execution_result.get('test_mode') else 'No'}")
    lines.append(f"Status: {execution_result.get('status', 'Unknown').upper()}")
    lines.append(f"Started: {execution_result.get('started_at', 'N/A')}")
    lines.append(f"Completed: {execution_result.get('completed_at', 'N/A')}")
    lines.append("")

    # Error details
    error = execution_result.get('error')
    if error:
        lines.append("-" * 40)
        lines.append("ERROR DETAILS:")
        lines.append("-" * 40)
        if isinstance(error, dict):
            lines.append(f"Type: {error.get('type', 'Unknown')}")
            lines.append(f"Message: {error.get('message', 'No message')}")
            if error.get('last_step'):
                lines.append(f"Last Step: {error.get('last_step')}")
        else:
            lines.append(f"Error: {error}")
        lines.append("")

    # Run status
    run_status = execution_result.get('run_status')
    if run_status:
        lines.append("-" * 40)
        lines.append("RUN STATUS:")
        lines.append("-" * 40)
        lines.append(f"Instance: {run_status.get('instance', 'N/A')}")
        lines.append(f"Status: {run_status.get('status', 'N/A')}")
        lines.append(f"Date Started: {run_status.get('date_started', 'N/A')}")
        lines.append(f"Date Completed: {run_status.get('date_completed', 'N/A')}")
        lines.append("")

    # Steps completed
    steps = execution_result.get('steps_completed', [])
    if steps:
        lines.append("-" * 40)
        lines.append("EXECUTION STEPS:")
        lines.append("-" * 40)
        for i, step in enumerate(reversed(steps), 1):  # Show in chronological order
            timestamp = step.get('timestamp', '')
            lines.append(f"{i}. {step.get('step', 'Unknown step')}")
            if timestamp:
                lines.append(f"   Timestamp: {timestamp}")
        lines.append("")

    # Delta counts
    delta_counts = execution_result.get('delta_counts', {})
    if delta_counts:
        lines.append("-" * 40)
        lines.append("DELTA TABLE RECORD COUNTS:")
        lines.append("-" * 40)
        total = 0
        for table, count in sorted(delta_counts.items()):
            if count >= 0:
                lines.append(f"  {table}: {count:,}")
                total += count
            else:
                lines.append(f"  {table}: (table not found)")
        lines.append(f"  TOTAL: {total:,}")
        lines.append("")

    lines.append("=" * 80)

    return "\n".join(lines)
