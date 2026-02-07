"""
HCM_MAIN_INTF Procedure Runner Service

This script runs on the SQL Server machine and monitors for procedure execution requests.
It keeps a persistent connection open while the stored procedure runs, solving the
Lambda timeout issue.

Setup:
1. Copy this script to the SQL Server machine
2. Install dependencies: pip install pyodbc
3. Run as a service or scheduled task that starts on boot
4. The Lambda will insert requests into PROCEDURE_RUN_QUEUE table
5. This service picks up requests and executes them

Usage:
    python procedure_runner_service.py [--once]  # --once for single run mode
"""

import pyodbc
import time
import threading
import logging
from datetime import datetime
import sys
import os

# Configuration
# Using same credentials as Lambda (from AWS Secrets Manager: Hacienda_ERP_MSSQL_Production)
CONNECTION_STRING = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=10.0.151.32,1433;"
    "DATABASE=Hacienda_ERP;"
    "UID=lambda_functions;"
    "PWD=coPPer873;"
    "Encrypt=yes;"
    "TrustServerCertificate=yes;"
)

POLL_INTERVAL = 10  # Check for new requests every 10 seconds
LOG_FILE = "procedure_runner.log"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def create_queue_table(cursor):
    """Create the procedure run queue table if it doesn't exist."""
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
                RequestedBy NVARCHAR(255) NULL
            )

            CREATE INDEX IX_PROCEDURE_RUN_QUEUE_Status
            ON dbo.PROCEDURE_RUN_QUEUE(Status, RequestedAt)

            PRINT 'Created PROCEDURE_RUN_QUEUE table'
        END
    """)
    cursor.commit()


def get_pending_request(cursor):
    """Get the oldest pending request."""
    cursor.execute("""
        SELECT TOP 1 RequestID, ProcedureName, TestExecution
        FROM dbo.PROCEDURE_RUN_QUEUE
        WHERE Status = 'PENDING'
        ORDER BY RequestedAt ASC
    """)
    return cursor.fetchone()


def update_request_status(cursor, request_id, status, error_message=None):
    """Update the status of a request."""
    if status == 'RUNNING':
        cursor.execute("""
            UPDATE dbo.PROCEDURE_RUN_QUEUE
            SET Status = ?, StartedAt = GETDATE()
            WHERE RequestID = ?
        """, (status, request_id))
    elif status in ('COMPLETED', 'FAILED'):
        cursor.execute("""
            UPDATE dbo.PROCEDURE_RUN_QUEUE
            SET Status = ?, CompletedAt = GETDATE(), ErrorMessage = ?
            WHERE RequestID = ?
        """, (status, error_message, request_id))
    else:
        cursor.execute("""
            UPDATE dbo.PROCEDURE_RUN_QUEUE
            SET Status = ?
            WHERE RequestID = ?
        """, (status, request_id))
    cursor.commit()


def run_procedure(procedure_name, test_execution):
    """
    Run the stored procedure with a persistent connection.
    This is the key function - it keeps the connection open until the procedure completes.
    """
    logger.info(f"Starting procedure: {procedure_name} (test_execution={test_execution})")

    # Create a dedicated connection with no timeout
    conn = pyodbc.connect(CONNECTION_STRING, timeout=0)
    conn.autocommit = True
    cursor = conn.cursor()

    try:
        # Clear ProcTrace for fresh logging
        cursor.execute("DELETE FROM dbo.ProcTrace")

        # Execute the stored procedure - this blocks until complete
        logger.info(f"Executing: EXEC dbo.{procedure_name} @test_execution = '{test_execution}'")
        cursor.execute(f"EXEC dbo.{procedure_name} @test_execution = '{test_execution}'")

        # Consume any result sets to ensure procedure fully completes
        while cursor.nextset():
            pass

        logger.info(f"Procedure {procedure_name} completed successfully")
        return True, None

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Procedure {procedure_name} failed: {error_msg}")
        return False, error_msg

    finally:
        cursor.close()
        conn.close()


def process_request(request_id, procedure_name, test_execution):
    """Process a single request."""
    # Create a new connection for status updates
    conn = pyodbc.connect(CONNECTION_STRING, timeout=30)
    conn.autocommit = True
    cursor = conn.cursor()

    try:
        # Mark as running
        update_request_status(cursor, request_id, 'RUNNING')
        logger.info(f"Processing request {request_id}: {procedure_name}")

        # Run the procedure (this blocks until complete)
        success, error_message = run_procedure(procedure_name, test_execution)

        # Update final status
        if success:
            update_request_status(cursor, request_id, 'COMPLETED')
            logger.info(f"Request {request_id} completed successfully")
        else:
            update_request_status(cursor, request_id, 'FAILED', error_message)
            logger.error(f"Request {request_id} failed: {error_message}")

    except Exception as e:
        logger.error(f"Error processing request {request_id}: {e}")
        update_request_status(cursor, request_id, 'FAILED', str(e))

    finally:
        cursor.close()
        conn.close()


def main_loop(run_once=False):
    """Main service loop - poll for requests and process them."""
    logger.info("Procedure Runner Service starting...")

    # Setup connection for polling
    conn = pyodbc.connect(CONNECTION_STRING, timeout=30)
    conn.autocommit = True
    cursor = conn.cursor()

    # Ensure queue table exists
    create_queue_table(cursor)

    logger.info("Service ready. Monitoring for procedure run requests...")

    while True:
        try:
            # Check for pending requests
            request = get_pending_request(cursor)

            if request:
                request_id, procedure_name, test_execution = request
                logger.info(f"Found pending request: {request_id}")

                # Process in current thread (blocking)
                # For parallel processing, could use threading here
                process_request(request_id, procedure_name, test_execution)

            if run_once:
                if not request:
                    logger.info("No pending requests found. Exiting (--once mode).")
                break

            # Wait before next poll
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Service stopped by user")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(POLL_INTERVAL)

    cursor.close()
    conn.close()
    logger.info("Service stopped.")


if __name__ == '__main__':
    run_once = '--once' in sys.argv
    main_loop(run_once=run_once)
