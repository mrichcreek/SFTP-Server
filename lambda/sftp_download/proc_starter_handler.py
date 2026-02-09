"""
Lambda handler for starting stored procedure asynchronously.
Used by Step Functions to initiate the HCM_MAIN_INTF procedure without waiting.
The procedure is started and the handler returns immediately - Step Functions will poll for status.
"""

import json
import os
import boto3
from datetime import datetime
from typing import Dict

try:
    from .sql_table_loader import get_aws_secret, parse_connection_string
except ImportError:
    from sftp_download.sql_table_loader import get_aws_secret, parse_connection_string


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


def clear_stuck_status(cursor) -> Dict:
    """
    Clear any stuck RUN_INTF_STATUS entries before starting a new procedure run.
    The procedure won't run if status is '01-InProgress' or '02-Completed'.

    Returns dict with previous status info if cleared.
    """
    result = {'cleared': False, 'previous_status': None}

    try:
        cursor.execute("""
            SELECT TOP 1 Instance, Status
            FROM RUN_INTF_STATUS
            ORDER BY Instance DESC
        """)
        row = cursor.fetchone()

        if row:
            instance, status = row
            result['previous_instance'] = instance
            result['previous_status'] = status

            if status and status.strip() in ('01-InProgress', '02-Completed'):
                cursor.execute("""
                    UPDATE RUN_INTF_STATUS
                    SET Status = '03-File Sent', DateCompleted = SYSDATETIME()
                    WHERE Instance = ?
                """, instance)
                result['cleared'] = True
                result['new_status'] = '03-File Sent'
    except Exception as e:
        result['error'] = str(e)

    return result


def clear_proc_trace(cursor):
    """Clear the ProcTrace table before a new run for fresh logging."""
    try:
        cursor.execute("DELETE FROM dbo.ProcTrace")
    except Exception:
        pass  # Table might not exist


def start_procedure_async(event, context):
    """
    Lambda handler to start the HCM_MAIN_INTF stored procedure asynchronously.

    This is designed for Step Functions workflow:
    1. Clears any stuck status from previous runs
    2. Clears ProcTrace for fresh logging
    3. Inserts a request into PROCEDURE_RUN_QUEUE table
    4. SQL Server Agent Job picks up the request and executes with persistent connection
    5. Returns immediately with queue info

    Step Functions will then poll get_procedure_status() to wait for completion.

    Input (from Step Functions):
    {
        "action": "start_async",
        "procedure": "HCM_MAIN_INTF",
        "test_execution": "N" or "Y",
        "sql_secret": "Hacienda_ERP_MSSQL_Production",
        "database": "Hacienda_ERP"
    }

    Returns:
    {
        "statusCode": 200,
        "started_at": "2024-01-15T10:30:00",
        "database": "Hacienda_ERP",
        "test_execution": "N",
        "status": "queued",
        "request_id": 123
    }
    """
    import pyodbc

    try:
        # Get parameters - support both direct invocation and API Gateway
        if 'body' in event and event['body']:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event

        sql_secret = body.get('sql_secret', os.environ.get('SQL_SECRET_NAME', 'Hacienda_ERP_MSSQL_Production'))
        database = body.get('database', 'Hacienda_ERP')
        test_execution = body.get('test_execution', 'N')

        # Normalize test_execution to single char
        if isinstance(test_execution, bool):
            test_execution = 'Y' if test_execution else 'N'
        elif isinstance(test_execution, str):
            test_execution = 'Y' if test_execution.upper() in ('Y', 'YES', 'TRUE', '1') else 'N'

        # Get connection string from Secrets Manager
        connection_string = get_aws_secret(sql_secret)
        conn_params = parse_connection_string(connection_string)

        # Override database
        conn_params['database'] = database

        result = {
            'started_at': datetime.now().isoformat(),
            'database': database,
            'test_execution': test_execution,
            'server': conn_params['server'],
            'status': 'starting'
        }

        # Build pyodbc connection string
        pyodbc_conn_str = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={conn_params['server']},{conn_params.get('port', 1433)};"
            f"DATABASE={database};"
            f"UID={conn_params['user']};"
            f"PWD={conn_params['password']};"
            "TrustServerCertificate=yes;"
            "Connection Timeout=60"
        )

        # Connect with autocommit for procedure execution
        conn = pyodbc.connect(pyodbc_conn_str, timeout=60, autocommit=True)
        cursor = conn.cursor()

        # Clear any stuck status
        status_clear_result = clear_stuck_status(cursor)
        result['status_cleared'] = status_clear_result

        # Clear ProcTrace for fresh logging
        clear_proc_trace(cursor)

        # Create queue table if it doesn't exist
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'PROCEDURE_RUN_QUEUE')
            BEGIN
                CREATE TABLE dbo.PROCEDURE_RUN_QUEUE (
                    RequestID INT IDENTITY(1,1) PRIMARY KEY,
                    ProcedureName NVARCHAR(255) NOT NULL,
                    TestExecution CHAR(1) DEFAULT 'N',
                    Status NVARCHAR(50) DEFAULT 'PENDING',
                    RequestedAt DATETIME DEFAULT GETDATE(),
                    StartedAt DATETIME NULL,
                    CompletedAt DATETIME NULL,
                    ErrorMessage NVARCHAR(MAX) NULL,
                    RequestedBy NVARCHAR(255) DEFAULT 'Lambda'
                )
            END
        """)

        # Insert procedure request into queue
        # SQL Server Agent Job will pick this up and execute with persistent connection
        cursor.execute("""
            INSERT INTO dbo.PROCEDURE_RUN_QUEUE (ProcedureName, TestExecution, RequestedBy)
            VALUES (?, ?, 'Lambda-StepFunctions')
        """, ('HCM_MAIN_INTF', test_execution))

        # Get the request ID
        cursor.execute("SELECT SCOPE_IDENTITY()")
        row = cursor.fetchone()
        request_id = int(row[0]) if row and row[0] else None

        cursor.close()
        conn.close()

        result['status'] = 'queued'
        result['request_id'] = request_id
        result['message'] = 'Procedure request queued. SQL Server Agent Job will execute. Poll status endpoint to track progress.'

        # For Step Functions direct invoke, return result directly
        if 'body' not in event:
            return result

        # For API Gateway, wrap in response
        return make_response(200, result)

    except Exception as e:
        error_result = {
            'status': 'error',
            'error': str(e),
            'error_type': type(e).__name__
        }

        if 'body' not in event:
            return error_result

        return make_response(500, error_result)


def check_procedure_running(event, context):
    """
    Lambda handler to check if the stored procedure is currently running.
    Used by Step Functions to determine if we need to wait.

    Returns status from RUN_INTF_STATUS table:
    - "01-InProgress": Procedure is running
    - "02-Completed": Procedure finished successfully
    - "03-File Sent": Previous run completed and files were uploaded
    - "error": Check failed
    """
    import pyodbc

    try:
        if 'body' in event and event['body']:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event

        sql_secret = body.get('sql_secret', os.environ.get('SQL_SECRET_NAME', 'Hacienda_ERP_MSSQL_Production'))
        database = body.get('database', 'Hacienda_ERP')

        connection_string = get_aws_secret(sql_secret)
        conn_params = parse_connection_string(connection_string)
        conn_params['database'] = database

        # Build pyodbc connection string
        pyodbc_conn_str = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={conn_params['server']},{conn_params.get('port', 1433)};"
            f"DATABASE={database};"
            f"UID={conn_params['user']};"
            f"PWD={conn_params['password']};"
            "TrustServerCertificate=yes;"
            "Connection Timeout=30"
        )

        conn = pyodbc.connect(pyodbc_conn_str, timeout=30)
        cursor = conn.cursor()

        # Get current status
        cursor.execute("""
            SELECT TOP 1 Instance, Status, DateStarted, DateCompleted
            FROM RUN_INTF_STATUS
            ORDER BY Instance DESC
        """)
        row = cursor.fetchone()

        result = {
            'checked_at': datetime.now().isoformat(),
            'database': database
        }

        if row:
            result['instance'] = row[0]
            result['status'] = row[1] if row[1] else 'unknown'
            result['date_started'] = str(row[2]) if row[2] else None
            result['date_completed'] = str(row[3]) if row[3] else None
            result['is_running'] = result['status'] == '01-InProgress'
            result['is_completed'] = result['status'] == '02-Completed'
        else:
            result['status'] = 'no_status_found'
            result['is_running'] = False
            result['is_completed'] = False

        # Get recent ProcTrace entries for progress info
        try:
            cursor.execute("""
                SELECT TOP 5 step
                FROM dbo.ProcTrace
                ORDER BY COALESCE(timestamp, GETDATE()) DESC
            """)
            trace_rows = cursor.fetchall()
            result['recent_steps'] = [r[0] for r in trace_rows]
        except Exception:
            result['recent_steps'] = []

        cursor.close()
        conn.close()

        if 'body' not in event:
            return result

        return make_response(200, result)

    except Exception as e:
        error_result = {
            'status': 'error',
            'error': str(e),
            'is_running': False,
            'is_completed': False
        }

        if 'body' not in event:
            return error_result

        return make_response(500, error_result)
