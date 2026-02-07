-- =============================================
-- SQL Server Agent Job for HCM_MAIN_INTF Procedure Runner
--
-- This job monitors the PROCEDURE_RUN_QUEUE table and executes
-- pending stored procedure requests. It runs every 30 seconds.
--
-- Run this script on the SQL Server to set up the job.
-- =============================================

USE [msdb]
GO

-- First, ensure the queue table exists in Hacienda_ERP
USE [Hacienda_ERP]
GO

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
    PRINT 'Created PROCEDURE_RUN_QUEUE table'
END
GO

-- Create the stored procedure that processes queue requests
IF EXISTS (SELECT * FROM sys.procedures WHERE name = 'ProcessProcedureQueue')
    DROP PROCEDURE dbo.ProcessProcedureQueue
GO

CREATE PROCEDURE dbo.ProcessProcedureQueue
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @RequestID INT
    DECLARE @ProcedureName NVARCHAR(255)
    DECLARE @TestExecution CHAR(1)
    DECLARE @ErrorMsg NVARCHAR(MAX)

    -- Get the oldest pending request
    SELECT TOP 1
        @RequestID = RequestID,
        @ProcedureName = ProcedureName,
        @TestExecution = TestExecution
    FROM dbo.PROCEDURE_RUN_QUEUE
    WHERE Status = 'PENDING'
    ORDER BY RequestID ASC

    -- If no pending requests, exit
    IF @RequestID IS NULL
        RETURN

    -- Mark as running
    UPDATE dbo.PROCEDURE_RUN_QUEUE
    SET Status = 'RUNNING',
        StartedAt = GETDATE()
    WHERE RequestID = @RequestID

    -- Clear ProcTrace for fresh logging
    BEGIN TRY
        DELETE FROM dbo.ProcTrace
    END TRY
    BEGIN CATCH
        -- Table might not exist, ignore
    END CATCH

    -- Reset RUN_INTF_STATUS if stuck
    BEGIN TRY
        UPDATE dbo.RUN_INTF_STATUS
        SET Status = '00-Ready'
        WHERE Instance = (SELECT MAX(Instance) FROM dbo.RUN_INTF_STATUS)
        AND Status IN ('01-InProgress', '02-Completed')
    END TRY
    BEGIN CATCH
        -- Ignore errors
    END CATCH

    -- Execute the stored procedure
    BEGIN TRY
        IF @ProcedureName = 'HCM_MAIN_INTF'
        BEGIN
            EXEC dbo.HCM_MAIN_INTF @test_execution = @TestExecution
        END

        -- Mark as completed
        UPDATE dbo.PROCEDURE_RUN_QUEUE
        SET Status = 'COMPLETED',
            CompletedAt = GETDATE()
        WHERE RequestID = @RequestID

    END TRY
    BEGIN CATCH
        SET @ErrorMsg = ERROR_MESSAGE()

        -- Mark as failed
        UPDATE dbo.PROCEDURE_RUN_QUEUE
        SET Status = 'FAILED',
            CompletedAt = GETDATE(),
            ErrorMessage = @ErrorMsg
        WHERE RequestID = @RequestID
    END CATCH
END
GO

PRINT 'Created ProcessProcedureQueue stored procedure'
GO

-- Now create the SQL Server Agent Job
USE [msdb]
GO

-- Delete job if it exists
IF EXISTS (SELECT job_id FROM msdb.dbo.sysjobs WHERE name = N'Hacienda_ProcessProcedureQueue')
BEGIN
    EXEC msdb.dbo.sp_delete_job @job_name = N'Hacienda_ProcessProcedureQueue', @delete_unused_schedule = 1
    PRINT 'Deleted existing job'
END
GO

-- Create the job
EXEC msdb.dbo.sp_add_job
    @job_name = N'Hacienda_ProcessProcedureQueue',
    @enabled = 1,
    @description = N'Monitors PROCEDURE_RUN_QUEUE and executes pending HCM_MAIN_INTF requests from the web app.',
    @category_name = N'[Uncategorized (Local)]',
    @owner_login_name = N'sa'
GO

-- Add job step
EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'Hacienda_ProcessProcedureQueue',
    @step_name = N'Process Queue',
    @step_id = 1,
    @subsystem = N'TSQL',
    @command = N'EXEC dbo.ProcessProcedureQueue',
    @database_name = N'Hacienda_ERP',
    @retry_attempts = 0,
    @retry_interval = 0,
    @on_success_action = 1,
    @on_fail_action = 2
GO

-- Create schedule - run every 30 seconds
EXEC msdb.dbo.sp_add_schedule
    @schedule_name = N'Every30Seconds',
    @enabled = 1,
    @freq_type = 4,           -- Daily
    @freq_interval = 1,
    @freq_subday_type = 2,    -- Seconds
    @freq_subday_interval = 30,
    @active_start_date = 20260101,
    @active_end_date = 99991231,
    @active_start_time = 0,
    @active_end_time = 235959
GO

-- Attach schedule to job
EXEC msdb.dbo.sp_attach_schedule
    @job_name = N'Hacienda_ProcessProcedureQueue',
    @schedule_name = N'Every30Seconds'
GO

-- Add job to server
EXEC msdb.dbo.sp_add_jobserver
    @job_name = N'Hacienda_ProcessProcedureQueue',
    @server_name = N'(local)'
GO

PRINT '============================================='
PRINT 'SQL Server Agent Job created successfully!'
PRINT ''
PRINT 'The job "Hacienda_ProcessProcedureQueue" will:'
PRINT '  - Run every 30 seconds'
PRINT '  - Check PROCEDURE_RUN_QUEUE for pending requests'
PRINT '  - Execute HCM_MAIN_INTF when requests are found'
PRINT '  - Update queue status to COMPLETED or FAILED'
PRINT ''
PRINT 'No Python service needed - SQL Server Agent handles it!'
PRINT '============================================='
GO
