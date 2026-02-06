import React, { useState, useEffect, useRef, useCallback } from 'react';
import { startPipeline, getPipelineStatus, listPipelineExecutions } from '../services/api';

// Pipeline steps with their sub-tasks (some sub-tasks are expandable to show files/details)
const PIPELINE_STEPS = [
  {
    id: 'sftp_download',
    label: 'Download Files',
    reportFile: 'Downloaded Files.txt',
    subTasks: [
      { id: 'connect', label: 'SFTP Connection' },
      { id: 'discover', label: 'Discover Files', expandable: true, expandLabel: 'files identified' },
      { id: 'download', label: 'Download Files', expandable: true, expandLabel: 'files', showProgress: true }
    ]
  },
  {
    id: 'validation',
    label: 'Validations',
    reportFile: null,
    subTasks: [
      { id: 'duplicates', label: 'Duplicate/Obsolete Validation', reportFile: 'Duplicate-Obsolete Validation.txt', expandable: true },
      { id: 'completeness', label: 'Completeness Validation', reportFile: 'Completeness Validation.txt', showProgress: true },
      { id: 'filename', label: 'File Name Validation', reportFile: 'File Name Validation.txt' }
    ]
  },
  {
    id: 'sql_load',
    label: 'Load to Database',
    reportFile: 'Load Database Report.txt',
    subTasks: [
      { id: 'rhum', label: 'RHUM Tables', expandable: true, expandLabel: 'tables' },
      { id: 'hacienda', label: 'HACIENDA Tables', expandable: true, expandLabel: 'tables' }
    ]
  },
  {
    id: 'stored_procedure',
    label: 'Process Data',
    reportFile: 'Process Data Report.txt',
    subTasks: [
      { id: 'person', label: 'Update PERSON Hierarchy' },
      { id: 'business', label: 'Validate Business Rules' },
      { id: 'summary', label: 'Generate Summary Tables' }
    ]
  },
  {
    id: 'delta_export',
    label: 'Generate Export Files',
    reportFile: 'Generate Files Report.txt',
    subTasks: [
      { id: 'rhum', label: 'RHUM Files', expandable: true, expandLabel: 'files' },
      { id: 'hacienda', label: 'HACIENDA Files', expandable: true, expandLabel: 'files' }
    ]
  },
  {
    id: 'generate_report',
    label: 'Generate Final Report',
    reportFile: 'Pipeline Report.txt',
    subTasks: [
      { id: 'collect', label: 'Collect Statistics' },
      { id: 'generate', label: 'Generate Summary' }
    ]
  }
];

// Color scheme
const COLORS = {
  primary: '#2563eb',
  success: '#16a34a',
  error: '#dc2626',
  warning: '#f97316',
  bgLight: '#f8fafc',
  bgWhite: '#ffffff',
  border: '#e2e8f0',
  textPrimary: '#1e293b',
  textSecondary: '#64748b'
};

// Format duration
const formatDuration = (seconds) => {
  if (!seconds && seconds !== 0) return '--';
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  if (hrs > 0) return `${hrs}h ${mins}m ${secs}s`;
  if (mins > 0) return `${mins}m ${secs}s`;
  return `${secs}s`;
};

// Format bytes to human readable
const formatBytes = (bytes) => {
  if (!bytes) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

// Format date/time
const formatDateTime = (date) => {
  if (!date) return '--';
  const d = new Date(date);
  return d.toLocaleString('en-US', {
    month: 'numeric', day: 'numeric', year: 'numeric',
    hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true
  });
};

// Status Badge Component
const StatusBadge = ({ status }) => {
  const styles = {
    pass: { backgroundColor: '#dcfce7', color: '#16a34a', border: '1px solid #86efac' },
    fail: { backgroundColor: '#fee2e2', color: '#dc2626', border: '1px solid #fca5a5' },
    'in progress': { backgroundColor: '#dbeafe', color: '#2563eb', border: '1px solid #93c5fd' },
    pending: { backgroundColor: '#f1f5f9', color: '#64748b', border: '1px solid #cbd5e1' }
  };
  const style = styles[status?.toLowerCase()] || styles.pending;
  return (
    <span style={{
      padding: '4px 12px', borderRadius: '20px', fontSize: '12px',
      fontWeight: '600', textTransform: 'uppercase', ...style
    }}>
      {status || 'PENDING'}
    </span>
  );
};

// Report File Link Component
const ReportLink = ({ filename }) => {
  if (!filename) return null;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: '4px',
      color: COLORS.primary, fontSize: '13px', cursor: 'pointer'
    }}>
      <span>📄</span>
      <span style={{ textDecoration: 'underline' }}>{filename}</span>
    </span>
  );
};

// Info Message Component (orange text)
const InfoMessage = ({ message, onClick, expandable }) => {
  if (!message) return null;
  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: '6px',
        color: COLORS.warning, fontSize: '13px', marginTop: '4px',
        cursor: expandable ? 'pointer' : 'default'
      }}
    >
      <span>ⓘ</span>
      <span>{message}</span>
      {expandable && <span style={{ fontSize: '10px' }}>▼</span>}
    </div>
  );
};

// File List Component (shown when sub-task is expanded)
const FileList = ({ files, label }) => {
  if (!files || files.length === 0) return null;
  return (
    <div style={{
      marginTop: '12px', marginLeft: '24px', padding: '12px',
      backgroundColor: '#f8fafc', borderRadius: '6px', border: '1px solid #e2e8f0'
    }}>
      <div style={{ fontSize: '12px', color: COLORS.textSecondary, marginBottom: '8px', fontWeight: '600' }}>
        {label || 'Files'} ({files.length})
      </div>
      <div style={{ maxHeight: '200px', overflowY: 'auto' }}>
        {files.map((file, idx) => (
          <div key={idx} style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '6px 8px', fontSize: '13px',
            backgroundColor: idx % 2 === 0 ? 'transparent' : '#f1f5f9',
            borderRadius: '4px'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ color: '#64748b' }}>📄</span>
              <span style={{ fontFamily: 'monospace', color: COLORS.textPrimary }}>{file.name || file}</span>
            </div>
            {file.size && (
              <span style={{ color: COLORS.textSecondary, fontSize: '12px' }}>{file.size}</span>
            )}
            {file.status && (
              <span style={{
                color: file.status === 'completed' ? COLORS.success : COLORS.primary,
                fontSize: '12px'
              }}>
                {file.status === 'completed' ? '✓' : '...'}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

// Sub-Task Component (now expandable)
const SubTask = ({ subTask, subTaskData, stepNumber, subIndex, onToggleExpand }) => {
  const status = subTaskData?.status || 'pending';
  const duration = subTaskData?.duration;
  const progress = subTaskData?.progress;
  const message = subTaskData?.message;
  const isExpanded = subTaskData?.isExpanded;
  const files = subTaskData?.files || [];
  const count = subTaskData?.count;

  // Generate info message
  let infoMessage = message;
  if (!infoMessage && count !== undefined && subTask.expandLabel) {
    infoMessage = `${count} ${subTask.expandLabel}`;
  }

  return (
    <div style={{
      backgroundColor: status === 'fail' ? '#fef2f2' : COLORS.bgWhite,
      borderRadius: '8px', padding: '12px 16px', marginLeft: '24px',
      border: `1px solid ${status === 'fail' ? '#fecaca' : COLORS.border}`
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ flex: 1 }}>
          <div
            style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: subTask.expandable ? 'pointer' : 'default' }}
            onClick={() => subTask.expandable && onToggleExpand && onToggleExpand()}
          >
            {subTask.expandable && (
              <span style={{
                color: COLORS.textSecondary, fontSize: '10px',
                transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
                transition: 'transform 0.2s'
              }}>▶</span>
            )}
            <span style={{ fontWeight: '500', color: COLORS.textPrimary }}>
              Step {stepNumber}.{subIndex + 1} - {subTask.label}
            </span>
          </div>

          {infoMessage && (
            <InfoMessage
              message={infoMessage}
              expandable={subTask.expandable && files.length > 0}
              onClick={() => subTask.expandable && onToggleExpand && onToggleExpand()}
            />
          )}

          {/* Progress bar for long-running tasks */}
          {subTask.showProgress && progress !== undefined && (status === 'in progress' || status === 'pass') && (
            <div style={{ marginTop: '8px' }}>
              <div style={{
                height: '6px', backgroundColor: '#e2e8f0', borderRadius: '3px',
                overflow: 'hidden', marginBottom: '4px'
              }}>
                <div style={{
                  height: '100%', width: `${progress}%`,
                  backgroundColor: status === 'pass' ? COLORS.success : COLORS.primary,
                  borderRadius: '3px', transition: 'width 0.3s ease'
                }} />
              </div>
              <div style={{ textAlign: 'right', fontSize: '12px', color: COLORS.textSecondary }}>
                {progress}%
              </div>
            </div>
          )}

          {/* Expanded file list */}
          {isExpanded && files.length > 0 && (
            <FileList files={files} label={subTask.expandLabel} />
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {duration !== undefined && (
            <span style={{
              display: 'flex', alignItems: 'center', gap: '4px',
              color: COLORS.textSecondary, fontSize: '13px'
            }}>
              <span>⏱</span>
              {formatDuration(duration)}
            </span>
          )}
          {subTask.reportFile && <ReportLink filename={subTask.reportFile} />}
          <StatusBadge status={status} />
        </div>
      </div>
    </div>
  );
};

// Step Card Component
const StepCard = ({ step, stepNumber, stepData, isExpanded, onToggle, onToggleSubTask }) => {
  const status = stepData?.status || 'pending';
  const duration = stepData?.duration;
  const subTasks = stepData?.subTasks || {};

  const getStepIcon = () => {
    if (status === 'pass' || status === 'completed') return '✓';
    if (status === 'fail') return '✗';
    if (status === 'in progress') return '⏳';
    return '○';
  };

  const getIconColor = () => {
    if (status === 'pass' || status === 'completed') return COLORS.success;
    if (status === 'fail') return COLORS.error;
    if (status === 'in progress') return COLORS.primary;
    return COLORS.textSecondary;
  };

  return (
    <div style={{
      backgroundColor: COLORS.bgWhite, borderRadius: '12px',
      border: `1px solid ${COLORS.border}`, marginBottom: '12px', overflow: 'hidden'
    }}>
      {/* Step Header */}
      <div
        onClick={onToggle}
        style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '16px 20px', cursor: 'pointer',
          backgroundColor: status === 'in progress' ? '#f0f7ff' : 'transparent'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <span style={{
            transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
            transition: 'transform 0.2s', color: COLORS.textSecondary
          }}>▶</span>
          <span style={{
            width: '28px', height: '28px', borderRadius: '50%',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            backgroundColor: status === 'pending' ? COLORS.bgLight : 'transparent',
            border: `2px solid ${getIconColor()}`, color: getIconColor(),
            fontSize: '14px', fontWeight: 'bold'
          }}>
            {getStepIcon()}
          </span>
          <span style={{ fontWeight: '600', fontSize: '15px', color: COLORS.textPrimary }}>
            Step {stepNumber} - {step.label}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          {duration !== undefined && (
            <span style={{
              display: 'flex', alignItems: 'center', gap: '4px',
              color: COLORS.textSecondary, fontSize: '13px'
            }}>
              <span>⏱</span>
              {formatDuration(duration)}
            </span>
          )}
          {step.reportFile && <ReportLink filename={step.reportFile} />}
          <StatusBadge status={status === 'completed' ? 'pass' : status} />
        </div>
      </div>

      {/* Expanded Content */}
      {isExpanded && (
        <div style={{ padding: '0 20px 16px 20px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {step.subTasks.map((subTask, idx) => (
            <SubTask
              key={subTask.id}
              subTask={subTask}
              subTaskData={subTasks[subTask.id]}
              stepNumber={stepNumber}
              subIndex={idx}
              onToggleExpand={() => onToggleSubTask(step.id, subTask.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
};

// Process History List Item
const HistoryListItem = ({ process, onClick }) => {
  const status = process.status === 'SUCCEEDED' ? 'Pass' :
                 process.status === 'FAILED' ? 'Fail' : 'In Progress';
  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '16px 20px', borderBottom: `1px solid ${COLORS.border}`,
        cursor: 'pointer', transition: 'background-color 0.2s'
      }}
      onMouseEnter={(e) => e.currentTarget.style.backgroundColor = COLORS.bgLight}
      onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <span style={{
          width: '24px', height: '24px', borderRadius: '50%',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          backgroundColor: status === 'Pass' ? '#dcfce7' : status === 'Fail' ? '#fee2e2' : '#dbeafe',
          color: status === 'Pass' ? COLORS.success : status === 'Fail' ? COLORS.error : COLORS.primary,
          fontSize: '12px'
        }}>
          {status === 'Pass' ? '✓' : status === 'Fail' ? '✗' : '⏳'}
        </span>
        <div>
          <div style={{ fontWeight: '600', color: COLORS.textPrimary }}>
            Process #{process.id || process.executionId?.split(':').pop()?.slice(0, 15)}
          </div>
          <div style={{ fontSize: '13px', color: COLORS.textSecondary }}>
            Started: {formatDateTime(process.startDate)}
          </div>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        <span style={{ color: COLORS.textSecondary, fontSize: '13px' }}>
          Duration: {formatDuration(process.duration)}
        </span>
        <StatusBadge status={status} />
      </div>
    </div>
  );
};

// Main Component
function PipelineInterface() {
  const [activeTab, setActiveTab] = useState('active');
  const [isRunning, setIsRunning] = useState(false);
  const [executionId, setExecutionId] = useState(null);
  const [processInfo, setProcessInfo] = useState(null);
  const [stepData, setStepData] = useState({});
  const [expandedSteps, setExpandedSteps] = useState({});
  const [overallProgress, setOverallProgress] = useState(0);
  const [error, setError] = useState(null);
  const [elapsedTime, setElapsedTime] = useState(0);
  const [historyList, setHistoryList] = useState([]);
  const [selectedHistory, setSelectedHistory] = useState(null);
  const [loadingHistory, setLoadingHistory] = useState(false);

  const timerRef = useRef(null);
  const pollingRef = useRef(null);
  const executionIdRef = useRef(null);
  const startTimeRef = useRef(null);
  const stepDataRef = useRef({});  // Persist step data across polls

  // Load history
  useEffect(() => {
    if (activeTab === 'history' && !selectedHistory) {
      loadHistoryList();
    }
  }, [activeTab, selectedHistory]);

  const loadHistoryList = async () => {
    setLoadingHistory(true);
    try {
      const executions = await listPipelineExecutions(20);
      setHistoryList(executions.executions || []);
    } catch (err) {
      console.error('Failed to load history:', err);
    }
    setLoadingHistory(false);
  };

  const viewHistoryDetail = async (process) => {
    setSelectedHistory(process);
    if (process.executionId) {
      try {
        const details = await getPipelineStatus(process.executionId);
        setSelectedHistory({ ...process, ...details });
      } catch (err) {
        console.error('Failed to load process details:', err);
      }
    }
  };

  const toggleStep = (stepId) => {
    setExpandedSteps(prev => ({ ...prev, [stepId]: !prev[stepId] }));
  };

  const toggleSubTask = (stepId, subTaskId) => {
    // Update both the state and the ref to persist across polls
    const updateFn = (prev) => ({
      ...prev,
      [stepId]: {
        ...prev[stepId],
        subTasks: {
          ...prev[stepId]?.subTasks,
          [subTaskId]: {
            ...prev[stepId]?.subTasks?.[subTaskId],
            isExpanded: !prev[stepId]?.subTasks?.[subTaskId]?.isExpanded
          }
        }
      }
    });
    setStepData(prev => {
      const newData = updateFn(prev);
      stepDataRef.current = newData;  // Keep ref in sync
      return newData;
    });
  };

  const mapStateToStepId = (stateName) => {
    const stateMap = {
      'SftpDownload': 'sftp_download',
      'CreateTimestampedFolder': 'sftp_download',
      'RunValidation': 'validation',
      'CheckValidationPassed': 'validation',
      'SqlLoad': 'sql_load',
      'StartStoredProcedure': 'stored_procedure',
      'WaitForProcedure': 'stored_procedure',
      'CheckProcedureStatus': 'stored_procedure',
      'IsProcedureComplete': 'stored_procedure',
      'ExportDeltaFiles': 'delta_export',
      'GenerateFinalReport': 'generate_report',
      'PipelineSuccess': 'generate_report'
    };
    return stateMap[stateName] || null;
  };

  const pollStatus = useCallback(async () => {
    const currentExecutionId = executionIdRef.current;
    if (!currentExecutionId) return;

    try {
      const status = await getPipelineStatus(currentExecutionId);
      const currentStepId = mapStateToStepId(status.current_step);

      // Get the step_details which contains all the detailed results
      const stepDetails = status.step_details || {};
      // The data is nested under the current step key (e.g., CheckProcedureStatus)
      const detailsData = stepDetails[status.current_step] || stepDetails[Object.keys(stepDetails)[0]] || {};

      setProcessInfo({
        id: currentExecutionId.split(':').pop()?.slice(0, 15),
        startDate: status.started_at || startTimeRef.current,
        endDate: status.stopped_at,
        status: status.status
      });

      // Build step data from the API response details
      // Preserve existing data to keep user interactions (like isExpanded)
      const prevData = stepDataRef.current;
      const newStepData = { ...prevData };

      // Helper to preserve isExpanded state from previous data
      const getExpandedState = (stepId, subTaskId) => {
        return prevData[stepId]?.subTasks?.[subTaskId]?.isExpanded || false;
      };

      // Extract SFTP Download results
      const sftpResult = detailsData.sftpDownloadResult;
      if (sftpResult) {
        const downloadDuration = sftpResult.completed_at && sftpResult.started_at
          ? Math.round((new Date(sftpResult.completed_at) - new Date(sftpResult.started_at)) / 1000)
          : 0;

        newStepData.sftp_download = {
          status: sftpResult.success ? 'pass' : 'fail',
          duration: downloadDuration,
          subTasks: {
            connect: {
              status: sftpResult.connected ? 'pass' : (sftpResult.success ? 'pass' : 'fail'),
              duration: 2
            },
            discover: {
              status: sftpResult.total_files > 0 ? 'pass' : 'pending',
              count: sftpResult.total_files || sftpResult.files_downloaded || 0,
              files: (sftpResult.file_results || []).map(f => ({
                name: f.filename,
                size: formatBytes(f.source_size),
                status: f.success ? 'completed' : 'failed'
              })),
              isExpanded: getExpandedState('sftp_download', 'discover')
            },
            download: {
              status: sftpResult.files_downloaded > 0 ? 'pass' : 'pending',
              count: sftpResult.files_downloaded || 0,
              progress: sftpResult.files_downloaded > 0 ? 100 : 0,
              totalBytes: sftpResult.total_bytes || 0,
              files: (sftpResult.file_results || []).filter(f => f.success).map(f => ({
                name: f.filename,
                size: formatBytes(f.uploaded_size),
                status: 'completed'
              })),
              isExpanded: getExpandedState('sftp_download', 'download')
            }
          }
        };
      }

      // Extract Folder creation results (part of step 1 - Download)
      const folderResult = detailsData.folderResult;
      if (folderResult && folderResult.success) {
        // Keep sftp_download as pass if folder creation succeeded
        if (newStepData.sftp_download) {
          newStepData.sftp_download.folderName = folderResult.folder_name;
          newStepData.sftp_download.filesMoved = folderResult.files_moved;
        }
      }

      // Extract Validation results
      const validationResult = detailsData.validationResult;
      if (validationResult) {
        // Count valid files for display
        const validFileCount = validationResult.valid_files?.length || 0;

        newStepData.validation = {
          status: validationResult.validation_passed ? 'pass' : (validationResult.has_critical_errors ? 'fail' : 'pass'),
          duration: 0,
          subTasks: {
            duplicates: {
              status: validationResult.duplicate_check?.success ? 'pass' :
                      (validationResult.duplicate_check?.error ? 'warning' : 'pass'),
              count: validationResult.duplicate_check?.duplicate_count || 0,
              message: validationResult.duplicate_check?.error || `${validFileCount} files validated`,
              isExpanded: getExpandedState('validation', 'duplicates')
            },
            completeness: {
              status: validationResult.completeness_check?.success ? 'pass' : 'warning',
              progress: 100,
              message: `${validFileCount} files complete`
            },
            filename: {
              status: validationResult.name_validation?.success ? 'pass' : 'warning',
              message: validationResult.name_validation?.error || 'File names validated'
            }
          }
        };
      }

      // Extract SQL Load results
      const sqlLoadResult = detailsData.sqlLoadResult;
      if (sqlLoadResult) {
        // Separate files by source (RHUM vs HACIENDA/FIMAS)
        const loadResults = sqlLoadResult.load_results || [];
        const rhumFiles = loadResults.filter(f => f.filename?.toLowerCase().includes('rhum'));
        const haciendaFiles = loadResults.filter(f => !f.filename?.toLowerCase().includes('rhum'));

        newStepData.sql_load = {
          status: sqlLoadResult.success ? 'pass' : (sqlLoadResult.files_processed > 0 ? 'warning' : 'fail'),
          duration: 0,
          filesProcessed: sqlLoadResult.files_processed || 0,
          filesLoaded: sqlLoadResult.files_loaded || 0,
          totalRows: sqlLoadResult.total_rows || 0,
          reportKey: sqlLoadResult.report_key,
          subTasks: {
            rhum: {
              status: rhumFiles.length > 0 ? 'pass' : 'pending',
              count: rhumFiles.length,
              files: rhumFiles.map(f => ({
                name: f.filename,
                status: f.success ? 'completed' : 'failed'
              })),
              isExpanded: getExpandedState('sql_load', 'rhum')
            },
            hacienda: {
              status: haciendaFiles.length > 0 ? 'pass' : 'pending',
              count: haciendaFiles.length,
              files: haciendaFiles.map(f => ({
                name: f.filename,
                status: f.success ? 'completed' : 'failed'
              })),
              isExpanded: getExpandedState('sql_load', 'hacienda')
            }
          }
        };
      }

      // Extract Stored Procedure results
      const procStartResult = detailsData.procStartResult;
      const procStatus = detailsData.procStatus;
      if (procStartResult || procStatus) {
        const isRunning = procStatus && !procStatus.is_completed;
        const startTime = procStartResult?.timestamp ? new Date(procStartResult.timestamp) : null;
        const procDuration = startTime ? Math.round((new Date() - startTime) / 1000) : 0;

        // Calculate total delta records for display
        const deltaCounts = procStatus?.delta_counts || {};
        const totalDeltas = Object.values(deltaCounts).reduce((sum, count) => sum + (count || 0), 0);

        // Build delta file list for display
        const deltaFiles = Object.entries(deltaCounts).map(([table, count]) => ({
          name: table.replace('_DELTA', ''),
          status: count > 0 ? 'completed' : 'pending',
          count: count
        }));

        newStepData.stored_procedure = {
          status: procStatus?.is_completed ? 'pass' : (isRunning ? 'in progress' : 'pending'),
          duration: procDuration,
          currentStep: procStatus?.current_step || procStatus?.status || 'running',
          deltaCounts: deltaCounts,
          totalDeltas: totalDeltas,
          subTasks: {
            person: {
              status: isRunning || procStatus?.is_completed ? 'pass' : 'in progress',
              message: `Processing records...`
            },
            business: {
              status: isRunning ? 'in progress' : 'pending',
              message: totalDeltas > 0 ? `${totalDeltas} delta records generated` : 'Validating...',
              count: totalDeltas,
              files: deltaFiles,
              isExpanded: getExpandedState('stored_procedure', 'business')
            },
            summary: {
              status: procStatus?.is_completed ? 'pass' : 'pending',
              message: procStatus?.is_completed ? 'Summary tables generated' : 'Waiting...'
            }
          }
        };
      }

      // Update current step to 'in progress' if running
      if (currentStepId && status.status === 'RUNNING') {
        if (!newStepData[currentStepId]) {
          newStepData[currentStepId] = { status: 'in progress', duration: 0, subTasks: {} };
        } else {
          newStepData[currentStepId] = { ...newStepData[currentStepId], status: 'in progress' };
        }
        setExpandedSteps(prev => ({ ...prev, [currentStepId]: true }));
      }

      // Store in ref and update state
      stepDataRef.current = newStepData;
      setStepData(newStepData);

      // Calculate progress based on completed steps
      let completedCount = 0;
      if (newStepData.sftp_download?.status === 'pass') completedCount++;
      if (newStepData.validation?.status === 'pass') completedCount++;
      if (newStepData.sql_load?.status === 'pass' || newStepData.sql_load?.status === 'warning') completedCount++;
      if (newStepData.stored_procedure?.status === 'pass') completedCount++;
      if (newStepData.delta_export?.status === 'pass') completedCount++;
      if (newStepData.generate_report?.status === 'pass') completedCount++;

      setOverallProgress(Math.round((completedCount / PIPELINE_STEPS.length) * 100));

      if (status.status === 'SUCCEEDED') {
        setOverallProgress(100);
        stopPolling();
      } else if (status.status === 'FAILED' || status.status === 'TIMED_OUT' || status.status === 'ABORTED') {
        setError(status.error || status.cause || 'Pipeline failed');
        stopPolling();
      }
    } catch (err) {
      console.error('Error polling status:', err);
    }
  }, []);

  const stopPolling = () => {
    setIsRunning(false);
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; }
  };

  const handleStartPipeline = async () => {
    setIsRunning(true);
    setError(null);
    setStepData({});
    setExpandedSteps({});
    setOverallProgress(0);
    setElapsedTime(0);

    const now = new Date();
    startTimeRef.current = now;
    setProcessInfo({
      id: `${now.getFullYear()}${(now.getMonth()+1).toString().padStart(2,'0')}${now.getDate().toString().padStart(2,'0')}-${now.getHours().toString().padStart(2,'0')}${now.getMinutes().toString().padStart(2,'0')}`,
      startDate: now,
      status: 'RUNNING'
    });

    try {
      const response = await startPipeline({ test_execution: false });
      if (response.execution_id) {
        // Set both ref (for immediate use) and state (for re-renders)
        executionIdRef.current = response.execution_id;
        setExecutionId(response.execution_id);
        timerRef.current = setInterval(() => { setElapsedTime(prev => prev + 1); }, 1000);
        pollingRef.current = setInterval(pollStatus, 5000);
        // First poll after 2 seconds
        setTimeout(pollStatus, 2000);
      } else {
        throw new Error(response.error || 'Failed to start pipeline');
      }
    } catch (err) {
      setError(err.message);
      setIsRunning(false);
    }
  };

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  useEffect(() => {
    if (executionId && isRunning && !pollingRef.current) {
      pollingRef.current = setInterval(pollStatus, 10000);
    }
  }, [executionId, isRunning, pollStatus]);

  // Generate mock data for history detail view
  const generateHistoryStepData = (process) => {
    const data = {};
    const isFailed = process.status === 'FAILED';

    // Sample files for demonstration
    const sampleFiles = [
      'HCM_PERSON_RHUM_20260206.csv',
      'HCM_PERSON_HACIENDA_20260206.csv',
      'HCM_ASSIGNMENT_RHUM_20260206.csv',
      'HCM_ASSIGNMENT_HACIENDA_20260206.csv',
      'HCM_SALARY_RHUM_20260206.csv',
      'HCM_SALARY_HACIENDA_20260206.csv'
    ];

    PIPELINE_STEPS.forEach((step, idx) => {
      const stepFailed = isFailed && idx === 1; // Fail at validation for demo
      data[step.id] = {
        status: stepFailed ? 'fail' : (idx < (isFailed ? 1 : PIPELINE_STEPS.length) ? 'pass' : 'pending'),
        duration: stepFailed ? 0 : Math.floor(Math.random() * 300) + 30,
        subTasks: {}
      };

      step.subTasks.forEach((subTask, subIdx) => {
        const subFailed = stepFailed && subIdx === 0;
        data[step.id].subTasks[subTask.id] = {
          status: subFailed ? 'fail' : data[step.id].status,
          duration: Math.floor(Math.random() * 60) + 5,
          count: subTask.expandable ? sampleFiles.length : undefined,
          message: subFailed ? 'Multiple duplicate files detected' : undefined,
          files: subTask.expandable ? sampleFiles.map(f => ({ name: f, status: 'completed' })) : [],
          progress: subTask.showProgress ? 100 : undefined,
          isExpanded: false
        };
      });
    });

    return data;
  };

  return (
    <div style={{ maxWidth: '1000px', margin: '0 auto', padding: '20px' }}>
      <h1 style={{ fontSize: '24px', fontWeight: '700', color: COLORS.textPrimary, marginBottom: '20px' }}>
        Sterling Process Monitor
      </h1>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '24px' }}>
        <button
          onClick={() => { setActiveTab('active'); setSelectedHistory(null); }}
          style={{
            padding: '10px 24px', borderRadius: '8px', border: 'none',
            fontSize: '14px', fontWeight: '600', cursor: 'pointer',
            backgroundColor: activeTab === 'active' ? COLORS.primary : COLORS.bgLight,
            color: activeTab === 'active' ? 'white' : COLORS.textSecondary
          }}
        >
          Active Process
        </button>
        <button
          onClick={() => setActiveTab('history')}
          style={{
            padding: '10px 24px', borderRadius: '8px', border: 'none',
            fontSize: '14px', fontWeight: '600', cursor: 'pointer',
            backgroundColor: activeTab === 'history' ? COLORS.primary : COLORS.bgLight,
            color: activeTab === 'history' ? 'white' : COLORS.textSecondary
          }}
        >
          Process History
        </button>
      </div>

      {/* Active Process Tab */}
      {activeTab === 'active' && (
        <div style={{
          backgroundColor: COLORS.bgWhite, borderRadius: '12px',
          border: `1px solid ${COLORS.border}`, padding: '24px'
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '24px' }}>
            <div>
              <h2 style={{ fontSize: '18px', fontWeight: '600', color: COLORS.textPrimary, margin: '0 0 8px 0' }}>
                Current Process
              </h2>
              {processInfo && (
                <div style={{ fontSize: '14px', color: COLORS.textSecondary }}>
                  Started: {formatDateTime(processInfo.startDate)}
                  {processInfo.endDate && <> &nbsp;·&nbsp; Ended: {formatDateTime(processInfo.endDate)}</>}
                  <span style={{
                    display: 'inline-flex', alignItems: 'center', gap: '4px',
                    marginLeft: '16px', color: COLORS.primary, fontWeight: '600'
                  }}>
                    <span>⏱</span>
                    {formatDuration(elapsedTime)}
                  </span>
                </div>
              )}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
              {!isRunning && !processInfo && (
                <button
                  onClick={handleStartPipeline}
                  style={{
                    padding: '12px 24px', backgroundColor: COLORS.success, color: 'white',
                    border: 'none', borderRadius: '8px', fontSize: '14px', fontWeight: '600', cursor: 'pointer'
                  }}
                >
                  ▶ Start Pipeline
                </button>
              )}
              {processInfo && (
                <StatusBadge status={
                  processInfo.status === 'RUNNING' ? 'In Progress' :
                  processInfo.status === 'SUCCEEDED' ? 'Pass' :
                  processInfo.status === 'FAILED' ? 'Fail' : 'Pending'
                } />
              )}
            </div>
          </div>

          {/* Overall Progress */}
          {processInfo && (
            <div style={{ marginBottom: '24px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                <span style={{ fontSize: '14px', color: COLORS.textSecondary }}>Overall Progress</span>
                <span style={{ fontSize: '14px', fontWeight: '600', color: COLORS.textPrimary }}>{overallProgress}%</span>
              </div>
              <div style={{ height: '8px', backgroundColor: '#e2e8f0', borderRadius: '4px', overflow: 'hidden' }}>
                <div style={{
                  height: '100%', width: `${overallProgress}%`,
                  backgroundColor: error ? COLORS.error : overallProgress === 100 ? COLORS.success : COLORS.primary,
                  borderRadius: '4px', transition: 'width 0.5s ease'
                }} />
              </div>
            </div>
          )}

          {error && (
            <div style={{
              backgroundColor: '#fef2f2', border: '1px solid #fecaca',
              borderRadius: '8px', padding: '16px', marginBottom: '24px', color: COLORS.error
            }}>
              <strong>Error:</strong> {error}
            </div>
          )}

          {processInfo ? (
            <div>
              {PIPELINE_STEPS.map((step, idx) => (
                <StepCard
                  key={step.id}
                  step={step}
                  stepNumber={idx + 1}
                  stepData={stepData[step.id]}
                  isExpanded={expandedSteps[step.id] || false}
                  onToggle={() => toggleStep(step.id)}
                  onToggleSubTask={toggleSubTask}
                />
              ))}
            </div>
          ) : (
            <div style={{ textAlign: 'center', padding: '60px 20px', color: COLORS.textSecondary }}>
              <div style={{ fontSize: '48px', marginBottom: '16px' }}>📊</div>
              <p style={{ fontSize: '16px', margin: 0 }}>Click "Start Pipeline" to begin processing</p>
            </div>
          )}
        </div>
      )}

      {/* Process History List */}
      {activeTab === 'history' && !selectedHistory && (
        <div style={{ backgroundColor: COLORS.bgWhite, borderRadius: '12px', border: `1px solid ${COLORS.border}` }}>
          <div style={{ padding: '20px', borderBottom: `1px solid ${COLORS.border}` }}>
            <h2 style={{ fontSize: '18px', fontWeight: '600', color: COLORS.textPrimary, margin: '0 0 4px 0' }}>
              Process History
            </h2>
            <p style={{ fontSize: '14px', color: COLORS.textSecondary, margin: 0 }}>
              View all completed and failed processes
            </p>
          </div>
          {loadingHistory ? (
            <div style={{ padding: '40px', textAlign: 'center', color: COLORS.textSecondary }}>Loading...</div>
          ) : historyList.length > 0 ? (
            <div>
              {historyList.map((process, idx) => (
                <HistoryListItem key={process.executionId || idx} process={process} onClick={() => viewHistoryDetail(process)} />
              ))}
            </div>
          ) : (
            <div style={{ padding: '40px', textAlign: 'center', color: COLORS.textSecondary }}>
              No process history available
            </div>
          )}
        </div>
      )}

      {/* History Detail View */}
      {activeTab === 'history' && selectedHistory && (
        <div style={{
          backgroundColor: COLORS.bgWhite, borderRadius: '12px',
          border: `1px solid ${COLORS.border}`, padding: '24px'
        }}>
          <div
            onClick={() => setSelectedHistory(null)}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: '8px',
              color: COLORS.primary, cursor: 'pointer', marginBottom: '20px', fontSize: '14px'
            }}
          >
            ← Back to History
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '24px' }}>
            <div>
              <h2 style={{ fontSize: '18px', fontWeight: '600', color: COLORS.textPrimary, margin: '0 0 8px 0' }}>
                Process #{selectedHistory.id || selectedHistory.executionId?.split(':').pop()?.slice(0, 15)}
              </h2>
              <div style={{ fontSize: '14px', color: COLORS.textSecondary }}>
                Started: {formatDateTime(selectedHistory.startDate)}
                {selectedHistory.stopDate && <> &nbsp;·&nbsp; Ended: {formatDateTime(selectedHistory.stopDate)}</>}
                <span style={{
                  display: 'inline-flex', alignItems: 'center', gap: '4px',
                  marginLeft: '16px', color: COLORS.primary, fontWeight: '600'
                }}>
                  <span>⏱</span>
                  {formatDuration(selectedHistory.duration)}
                </span>
              </div>
            </div>
            <StatusBadge status={
              selectedHistory.status === 'SUCCEEDED' ? 'Pass' :
              selectedHistory.status === 'FAILED' ? 'Fail' : 'In Progress'
            } />
          </div>

          {/* Steps with mock data */}
          {(() => {
            const historyStepData = generateHistoryStepData(selectedHistory);
            return (
              <div>
                {PIPELINE_STEPS.map((step, idx) => (
                  <StepCard
                    key={step.id}
                    step={step}
                    stepNumber={idx + 1}
                    stepData={historyStepData[step.id]}
                    isExpanded={expandedSteps[step.id] || false}
                    onToggle={() => toggleStep(step.id)}
                    onToggleSubTask={(stepId, subTaskId) => {
                      // For history view, toggle expansion in the mock data
                      setExpandedSteps(prev => ({
                        ...prev,
                        [`${stepId}_${subTaskId}`]: !prev[`${stepId}_${subTaskId}`]
                      }));
                    }}
                  />
                ))}
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}

export default PipelineInterface;
