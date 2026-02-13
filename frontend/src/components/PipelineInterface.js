import React, { useState, useEffect, useRef, useCallback } from 'react';
import { startPipeline, getPipelineStatus, listPipelineExecutions, getReportDownloadUrl } from '../services/api';

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
    reportFile: 'Validation Report.txt',
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
      { id: 'rhum', label: 'RHUM Tables (8)', expandable: true, expandLabel: 'tables' },
      { id: 'hacienda', label: 'HACIENDA Tables (8)', expandable: true, expandLabel: 'tables' },
      { id: '911', label: '911 Tables (7)', expandable: true, expandLabel: 'tables' },
      { id: 'fimas', label: 'FIMAS Tables (8)', expandable: true, expandLabel: 'tables' },
      { id: 'kronospol', label: 'KRONOSPOL Tables (8)', expandable: true, expandLabel: 'tables' },
      { id: 'doe', label: 'DOE Tables (6)', expandable: true, expandLabel: 'tables' },
      { id: 'adppolicia', label: 'ADPPOLICIA Tables (1)', expandable: true, expandLabel: 'tables' },
      { id: 'kronosde', label: 'KRONOSDE Tables (1)', expandable: true, expandLabel: 'tables' },
      { id: 'sepi', label: 'SEPI Tables (1)', expandable: true, expandLabel: 'tables' }
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
      { id: 'rhum', label: 'INT012 Files (Hire/Rehire)', expandable: true, expandLabel: 'files' },
      { id: 'hacienda', label: 'Delta Files', expandable: true, expandLabel: 'files' }
    ]
  },
  {
    id: 'generate_report',
    label: 'Generate Final Report',
    reportFile: 'Pipeline Summary.txt',
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
const ReportLink = ({ filename, reportKey, folderName, onDownload }) => {
  if (!filename) return null;

  const handleClick = async (e) => {
    e.stopPropagation();
    if (onDownload) {
      onDownload(filename, reportKey, folderName);
    }
  };

  return (
    <span
      onClick={handleClick}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: '4px',
        color: COLORS.primary, fontSize: '13px', cursor: 'pointer'
      }}
    >
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
const SubTask = ({ subTask, subTaskData, stepNumber, subIndex, onToggleExpand, onDownloadReport, folderName }) => {
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
          {subTask.reportFile && (
            <ReportLink
              filename={subTask.reportFile}
              folderName={folderName}
              onDownload={onDownloadReport}
            />
          )}
          <StatusBadge status={status} />
        </div>
      </div>
    </div>
  );
};

// Step Card Component
const StepCard = ({ step, stepNumber, stepData, isExpanded, onToggle, onToggleSubTask, onDownloadReport, folderName }) => {
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
          {step.reportFile && (
            <ReportLink
              filename={step.reportFile}
              folderName={folderName}
              onDownload={onDownloadReport}
            />
          )}
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
              onDownloadReport={onDownloadReport}
              folderName={folderName}
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
      const data = await listPipelineExecutions(20);
      const executions = (data.executions || []).map(exec => ({
        // Normalize field names (support both old and new API format)
        executionId: exec.executionId || exec.execution_id,
        id: exec.id || exec.name,
        name: exec.name,
        status: exec.status,
        startDate: exec.startDate || exec.started_at,
        stopDate: exec.stopDate || exec.stopped_at,
        duration: exec.duration != null ? exec.duration :
          (exec.stopDate || exec.stopped_at) && (exec.startDate || exec.started_at)
            ? (new Date(exec.stopDate || exec.stopped_at) - new Date(exec.startDate || exec.started_at)) / 1000
            : null
      }));
      setHistoryList(executions);
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

      // Debug logging
      console.log('=== Pipeline Status Response ===');
      console.log('status.status:', status.status);
      console.log('status.current_step:', status.current_step);
      console.log('status.completed_states:', status.completed_states);
      console.log('step_details keys:', Object.keys(status.step_details || {}));

      const currentStepId = mapStateToStepId(status.current_step);

      // Get the step_details which contains all the detailed results
      // step_details is the accumulated state with all results (sftpDownloadResult, validationResult, etc.)
      const detailsData = status.step_details || {};

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

      // Track which steps have completed based on completed_states from API
      const completedStates = new Set(status.completed_states || []);
      const isSftpDone = completedStates.has('SftpDownload');
      // eslint-disable-next-line no-unused-vars
      const isFolderDone = completedStates.has('CreateTimestampedFolder');
      const isValidationDone = completedStates.has('RunValidation');
      const isSqlLoadDone = completedStates.has('SqlLoad');
      const isProcStartDone = completedStates.has('StartStoredProcedure');
      // eslint-disable-next-line no-unused-vars
      const isProcStatusDone = completedStates.has('CheckProcedureStatus');
      const isExportDone = completedStates.has('ExportDeltaFiles');
      const isUploadDone = completedStates.has('SftpUpload');
      const isReportDone = completedStates.has('GenerateFinalReport');
      const isPipelineSuccess = status.status === 'SUCCEEDED';

      // Extract SFTP Download results
      const sftpResult = detailsData.sftpDownloadResult;
      if (sftpResult) {
        const downloadDuration = sftpResult.completed_at && sftpResult.started_at
          ? Math.round((new Date(sftpResult.completed_at) - new Date(sftpResult.started_at)) / 1000)
          : 0;

        // Substeps update progressively based on actual progress
        const hasConnected = sftpResult.connected || sftpResult.success;
        const filesFound = sftpResult.total_files || sftpResult.files_downloaded || 0;
        const filesDownloaded = sftpResult.files_downloaded || 0;

        newStepData.sftp_download = {
          status: isSftpDone ? (sftpResult.success ? 'pass' : 'fail') : 'in progress',
          duration: downloadDuration,
          folderName: detailsData.folderResult?.folder_name,
          subTasks: {
            connect: {
              status: hasConnected ? 'pass' : (status.current_step === 'SftpDownload' ? 'in progress' : 'pending'),
              duration: hasConnected ? 2 : undefined
            },
            discover: {
              status: filesFound > 0 ? 'pass' : (hasConnected && status.current_step === 'SftpDownload' ? 'in progress' : 'pending'),
              count: filesFound,
              files: (sftpResult.file_results || []).map(f => ({
                name: f.filename,
                size: formatBytes(f.source_size),
                status: f.success ? 'completed' : 'failed'
              })),
              isExpanded: getExpandedState('sftp_download', 'discover')
            },
            download: {
              status: filesDownloaded > 0 ? 'pass' : (filesFound > 0 ? 'in progress' : 'pending'),
              count: filesDownloaded,
              progress: filesDownloaded > 0 ? 100 : (filesFound > 0 ? 50 : 0),
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
      } else if (status.current_step === 'SftpDownload') {
        // SFTP in progress but no results yet
        newStepData.sftp_download = {
          status: 'in progress',
          duration: 0,
          subTasks: {
            connect: { status: 'in progress' },
            discover: { status: 'pending', count: 0, files: [], isExpanded: false },
            download: { status: 'pending', count: 0, progress: 0, files: [], isExpanded: false }
          }
        };
      }

      // Extract Validation results
      const validationResult = detailsData.validationResult;
      if (validationResult) {
        // Count valid files for display
        const validFileCount = validationResult.valid_files?.length || 0;
        const dupCheck = validationResult.duplicate_check || {};
        const compCheck = validationResult.completeness_check || {};
        const nameCheck = validationResult.name_validation || {};

        newStepData.validation = {
          status: isValidationDone ? (validationResult.validation_passed ? 'pass' : (validationResult.has_critical_errors ? 'fail' : 'pass')) : 'in progress',
          duration: 0,
          subTasks: {
            duplicates: {
              status: dupCheck.success !== undefined ? (dupCheck.success ? 'pass' : 'fail') : (isValidationDone ? 'skipped' : 'pending'),
              count: dupCheck.duplicate_count || 0,
              message: dupCheck.error || (dupCheck.success ? `${validFileCount} files validated` : 'Checking...'),
              isExpanded: getExpandedState('validation', 'duplicates')
            },
            completeness: {
              status: compCheck.success !== undefined ? (compCheck.success ? 'pass' : 'fail') : (isValidationDone ? 'skipped' : 'pending'),
              progress: 100,
              message: compCheck.success ? `${validFileCount} files complete` : (compCheck.error || 'Checking...')
            },
            filename: {
              status: nameCheck.success !== undefined ? (nameCheck.success ? 'pass' : 'fail') : (isValidationDone ? 'skipped' : 'pending'),
              message: nameCheck.error || (nameCheck.success ? 'File names validated' : 'Checking...')
            }
          }
        };
      } else if (status.current_step === 'RunValidation') {
        newStepData.validation = {
          status: 'in progress',
          duration: 0,
          subTasks: {
            duplicates: { status: 'in progress', count: 0, message: 'Checking duplicates...', isExpanded: false },
            completeness: { status: 'pending', progress: 0, message: 'Waiting...' },
            filename: { status: 'pending', message: 'Waiting...' }
          }
        };
      }

      // Extract SQL Load results
      const sqlLoadResult = detailsData.sqlLoadResult;
      if (sqlLoadResult || isSqlLoadDone) {
        // Categorize files by source system
        const loadResults = sqlLoadResult?.load_results || [];
        const sourceKeys = ['rhum', 'hacienda', '911', 'fimas', 'kronospol', 'doe', 'adppolicia', 'kronosde', 'sepi'];
        const sourcePatterns = {
          rhum: f => /rhum/i.test(f.filename || ''),
          hacienda: f => /hac88|hacienda/i.test(f.filename || ''),
          '911': f => /_911_/i.test(f.filename || ''),
          fimas: f => /fimas/i.test(f.filename || ''),
          kronospol: f => /kronospol/i.test(f.filename || ''),
          doe: f => /_doe_/i.test(f.filename || ''),
          adppolicia: f => /adppolicia/i.test(f.filename || ''),
          kronosde: f => /kronosde/i.test(f.filename || ''),
          sepi: f => /sepi/i.test(f.filename || '')
        };

        // Group files by source
        const sourceFiles = {};
        sourceKeys.forEach(key => { sourceFiles[key] = []; });
        loadResults.forEach(f => {
          let matched = false;
          for (const key of sourceKeys) {
            if (sourcePatterns[key](f)) {
              sourceFiles[key].push(f);
              matched = true;
              break;
            }
          }
          if (!matched) {
            // Unmatched files go to hacienda as fallback
            sourceFiles.hacienda.push(f);
          }
        });

        // Determine success: explicit success property, or infer from tables_created/files_loaded
        const tablesLoaded = sqlLoadResult?.tables_created || sqlLoadResult?.files_loaded || 0;
        const hasExplicitSuccess = sqlLoadResult?.success !== undefined;
        const isSuccess = hasExplicitSuccess ? sqlLoadResult.success : (tablesLoaded > 0 || isSqlLoadDone);

        // Build subTasks for each source
        const subTasks = {};
        sourceKeys.forEach(key => {
          const files = sourceFiles[key];
          subTasks[key] = {
            status: files.length > 0 ? 'pass' : (isSqlLoadDone ? 'skipped' : 'pending'),
            count: files.length,
            message: files.length > 0 ? `${files.length} tables` : undefined,
            files: files.map(f => ({
              name: f.filename,
              status: f.success ? 'completed' : 'failed'
            })),
            isExpanded: getExpandedState('sql_load', key)
          };
        });

        newStepData.sql_load = {
          status: isSqlLoadDone ? (isSuccess ? 'pass' : 'fail') : 'in progress',
          duration: 0,
          filesProcessed: sqlLoadResult?.files_processed || 0,
          filesLoaded: sqlLoadResult?.files_loaded || tablesLoaded,
          totalRows: sqlLoadResult?.total_rows || 0,
          reportKey: sqlLoadResult?.report_key,
          subTasks
        };
      } else if (status.current_step === 'SqlLoad') {
        const subTasks = {};
        ['rhum', 'hacienda', '911', 'fimas', 'kronospol', 'doe', 'adppolicia', 'kronosde', 'sepi'].forEach((key, idx) => {
          subTasks[key] = { status: idx === 0 ? 'in progress' : 'pending', count: 0, files: [], isExpanded: false };
        });
        newStepData.sql_load = {
          status: 'in progress',
          duration: 0,
          subTasks
        };
      }

      // Extract Stored Procedure results
      const procStartResult = detailsData.procStartResult;
      const procStatus = detailsData.procStatus;
      // Check if procedure failed
      const isProcFailed = completedStates.has('ProcedureFailed') ||
                           procStatus?.status === 'error' ||
                           (status.status === 'FAILED' && completedStates.has('CheckProcedureStatus'));

      if (procStartResult || procStatus) {
        const isRunning = procStatus && !procStatus.is_completed && procStatus.status !== 'error' && !isProcFailed;
        const isCompleted = procStatus?.is_completed || false;
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

        // Determine substep statuses progressively
        const personStatus = isProcFailed ? 'fail' : (isProcStartDone ? 'pass' : (procStartResult ? 'in progress' : 'pending'));
        const businessStatus = isProcFailed ? 'fail' : (isCompleted ? 'pass' : (isRunning ? 'in progress' : 'pending'));
        const summaryStatus = isProcFailed ? 'fail' : (isCompleted ? 'pass' : 'pending');

        // Get error message if available
        const errorMessage = detailsData.errorReport?.message || procStatus?.error || detailsData._error?.cause;

        newStepData.stored_procedure = {
          status: isProcFailed ? 'fail' : (isCompleted ? 'pass' : (isRunning || isProcStartDone ? 'in progress' : 'pending')),
          duration: procDuration,
          currentStep: procStatus?.current_step || procStatus?.status || 'running',
          deltaCounts: deltaCounts,
          totalDeltas: totalDeltas,
          errorMessage: errorMessage,
          subTasks: {
            person: {
              status: personStatus,
              message: personStatus === 'fail' ? 'Error during processing' : (personStatus === 'pass' ? 'PERSON hierarchy updated' : 'Processing records...')
            },
            business: {
              status: businessStatus,
              message: isProcFailed ? (errorMessage || 'Procedure failed') : (totalDeltas > 0 ? `${totalDeltas} delta records generated` : (isRunning ? 'Validating business rules...' : 'Waiting...')),
              count: totalDeltas,
              files: deltaFiles,
              isExpanded: getExpandedState('stored_procedure', 'business')
            },
            summary: {
              status: summaryStatus,
              message: isProcFailed ? 'Not completed' : (isCompleted ? 'Summary tables generated' : 'Waiting...')
            }
          }
        };
      } else if (['StartStoredProcedure', 'WaitForProcedure', 'CheckProcedureStatus', 'ProcedureFailed'].includes(status.current_step)) {
        newStepData.stored_procedure = {
          status: 'in progress',
          duration: 0,
          subTasks: {
            person: { status: 'in progress', message: 'Starting procedure...' },
            business: { status: 'pending', message: 'Waiting...', count: 0, files: [], isExpanded: false },
            summary: { status: 'pending', message: 'Waiting...' }
          }
        };
      }

      // Extract Delta Export results
      const exportResult = detailsData.exportResult;
      if (exportResult || isExportDone) {
        // Handle 'files_exported' (array of objects with s3_key, filename, row_count)
        const filesExportedArray = exportResult?.files_exported || [];
        const exportedFilenames = filesExportedArray.map(f => f.filename || f.s3_key?.split('/').pop() || f);

        // Determine success: explicit success property, or infer from files exported
        const filesExported = exportResult?.total_files || filesExportedArray.length || 0;
        const totalRows = exportResult?.total_rows || filesExportedArray.reduce((sum, f) => sum + (f.row_count || 0), 0);
        const hasExplicitSuccess = exportResult?.success !== undefined;
        const isSuccess = hasExplicitSuccess ? exportResult.success : (filesExported > 0 || isExportDone);

        // Split files by INT012 (hire/rehire) vs normal - these are labeled RHUM/HACIENDA in UI but actually represent INT012/Normal split
        const int012Files = filesExportedArray.filter(f => (f.category === 'INT012' || (f.filename || '').includes('INT012')));
        const normalFiles = filesExportedArray.filter(f => (f.category === 'NORMAL' || (!(f.filename || '').includes('INT012') && f.category !== 'INT012')));

        newStepData.delta_export = {
          status: isExportDone ? (isSuccess ? 'pass' : 'fail') : 'in progress',
          duration: 0,
          outputPrefix: exportResult?.output_prefix,
          totalFiles: filesExported,
          totalRows: totalRows,
          subTasks: {
            rhum: {
              // This is actually INT012 files (new hires/rehires)
              status: int012Files.length > 0 ? 'pass' : (filesExported > 0 ? 'skipped' : (isExportDone ? 'skipped' : 'pending')),
              count: int012Files.length,
              message: int012Files.length > 0 ? `${int012Files.length} INT012 files` : (filesExported > 0 ? 'No INT012 records' : undefined),
              files: int012Files.map(f => ({ name: f.filename || f.s3_key?.split('/').pop(), status: 'completed' })),
              isExpanded: getExpandedState('delta_export', 'rhum')
            },
            hacienda: {
              // This is actually normal delta files
              status: normalFiles.length > 0 ? 'pass' : (isExportDone ? 'skipped' : 'pending'),
              count: normalFiles.length,
              message: normalFiles.length > 0 ? `${normalFiles.length} delta files` : undefined,
              files: normalFiles.map(f => ({ name: f.filename || f.s3_key?.split('/').pop(), status: 'completed' })),
              isExpanded: getExpandedState('delta_export', 'hacienda')
            }
          }
        };
      } else if (status.current_step === 'ExportDeltaFiles') {
        newStepData.delta_export = {
          status: 'in progress',
          duration: 0,
          subTasks: {
            rhum: { status: 'in progress', count: 0, files: [], isExpanded: false },
            hacienda: { status: 'pending', count: 0, files: [], isExpanded: false }
          }
        };
      }

      // Extract SFTP Upload results (part of delta export for display)
      const uploadResult = detailsData.uploadResult;
      if (uploadResult && newStepData.delta_export) {
        newStepData.delta_export.uploadResult = uploadResult;
        // Update status if upload completed
        if (isUploadDone) {
          newStepData.delta_export.status = uploadResult.success ? 'pass' : 'fail';
        }
      }

      // Extract Final Report results
      const finalReport = detailsData.finalReport;
      if (finalReport || isPipelineSuccess) {
        newStepData.generate_report = {
          status: isReportDone || isPipelineSuccess ? 'pass' : 'in progress',
          duration: 0,
          reportKey: finalReport?.report_key,
          subTasks: {
            collect: {
              status: isPipelineSuccess ? 'pass' : 'in progress',
              message: 'Statistics collected'
            },
            generate: {
              status: isPipelineSuccess ? 'pass' : 'pending',
              message: isPipelineSuccess ? 'Summary generated' : 'Generating...'
            }
          }
        };
      } else if (status.current_step === 'GenerateFinalReport') {
        newStepData.generate_report = {
          status: 'in progress',
          duration: 0,
          subTasks: {
            collect: { status: 'in progress', message: 'Collecting statistics...' },
            generate: { status: 'pending', message: 'Waiting...' }
          }
        };
      }

      // Update current step to 'in progress' if running and no data exists
      if (currentStepId && status.status === 'RUNNING') {
        if (!newStepData[currentStepId]) {
          newStepData[currentStepId] = { status: 'in progress', duration: 0, subTasks: {} };
        }
        setExpandedSteps(prev => ({ ...prev, [currentStepId]: true }));
      }

      // Store in ref and update state
      stepDataRef.current = newStepData;
      setStepData(newStepData);

      // Debug: log what step data was calculated
      console.log('=== Calculated Step Data ===');
      console.log('sftp_download status:', newStepData.sftp_download?.status);
      console.log('validation status:', newStepData.validation?.status);
      console.log('sql_load status:', newStepData.sql_load?.status, '| sqlLoadResult:', sqlLoadResult ? 'exists' : 'missing', '| isSqlLoadDone:', isSqlLoadDone);
      console.log('stored_procedure status:', newStepData.stored_procedure?.status);
      console.log('delta_export status:', newStepData.delta_export?.status, '| exportResult:', exportResult ? 'exists' : 'missing', '| isExportDone:', isExportDone);
      console.log('generate_report status:', newStepData.generate_report?.status);

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
        // Try to get a more descriptive error message
        let errorMsg = status.error || status.cause || 'Pipeline failed';
        if (detailsData._error?.cause) {
          errorMsg = detailsData._error.cause;
        } else if (detailsData.procStatus?.status === 'error') {
          errorMsg = `Stored procedure failed: ${detailsData.procStatus.error || 'Unknown error'}`;
        } else if (detailsData.errorReport?.message) {
          errorMsg = detailsData.errorReport.message;
        }
        setError(errorMsg);
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

  // Get the folder name from step data for report paths
  const getFolderName = () => {
    return stepData.sftp_download?.folderName || '';
  };

  // Handle report download
  const handleDownloadReport = async (filename, reportKey, folderName) => {
    try {
      // Construct the S3 key for the report
      // Reports are stored in the folder's reports directory
      const folder = folderName || getFolderName();
      if (!folder) {
        alert('Pipeline folder not available. Please wait for the download step to complete.');
        return;
      }

      // Map filenames to their S3 paths using correct folder structure
      // 2_Validation_Reports/ - validation reports (name, completeness, schema, summary)
      // 3_Load_Reports/ - SQL load reports
      // 6_Delta_Files/ - export files
      const reportPaths = {
        'Downloaded Files.txt': `${folder}/2_Validation_Reports/Downloaded Files.txt`,
        'Duplicate-Obsolete Validation.txt': `${folder}/2_Validation_Reports/Duplicate-Obsolete Validation.txt`,
        'Completeness Validation.txt': `${folder}/2_Validation_Reports/Completeness Validation.txt`,
        'File Name Validation.txt': `${folder}/2_Validation_Reports/File Name Validation.txt`,
        'Validation Report.txt': `${folder}/2_Validation_Reports/Validation Report.txt`,
        'Pipeline Summary.txt': `${folder}/2_Validation_Reports/Pipeline Summary.txt`,
        'Load Database Report.txt': `${folder}/3_Load_Reports/Load Database Report.txt`,
        'Process Data Report.txt': `${folder}/3_Load_Reports/Process Data Report.txt`,
        'Generate Files Report.txt': `${folder}/6_Delta_Files/Generate Files Report.txt`,
        'Pipeline Report.txt': `${folder}/2_Validation_Reports/Pipeline Summary.txt`
      };

      const s3Key = reportKey || reportPaths[filename] || `${folder}/2_Validation_Reports/${filename}`;

      // Get pre-signed URL for download
      const response = await getReportDownloadUrl(s3Key);

      if (response.url) {
        // Open the download URL in a new tab
        window.open(response.url, '_blank');
      } else if (response.error) {
        alert(`Report not available: ${response.error}`);
      }
    } catch (err) {
      console.error('Error downloading report:', err);
      alert(`Failed to download report: ${err.message}`);
    }
  };

  // Handle downloading full combined report
  const handleDownloadFullReport = async (folderName) => {
    if (!folderName) {
      alert('Pipeline folder not available.');
      return;
    }

    // List of all reports to combine
    const allReports = [
      { name: 'Downloaded Files.txt', path: `${folderName}/2_Validation_Reports/Downloaded Files.txt` },
      { name: 'Duplicate-Obsolete Validation.txt', path: `${folderName}/2_Validation_Reports/Duplicate-Obsolete Validation.txt` },
      { name: 'File Name Validation.txt', path: `${folderName}/2_Validation_Reports/File Name Validation.txt` },
      { name: 'Completeness Validation.txt', path: `${folderName}/2_Validation_Reports/Completeness Validation.txt` },
      { name: 'Pipeline Summary.txt', path: `${folderName}/2_Validation_Reports/Pipeline Summary.txt` },
      { name: 'Load Database Report.txt', path: `${folderName}/3_Load_Reports/Load Database Report.txt` },
      { name: 'Process Data Report.txt', path: `${folderName}/3_Load_Reports/Process Data Report.txt` },
      { name: 'Generate Files Report.txt', path: `${folderName}/6_Delta_Files/Generate Files Report.txt` }
    ];

    let combinedReport = '';
    combinedReport += '='.repeat(80) + '\n';
    combinedReport += 'HACIENDA ERP PIPELINE - FULL REPORT\n';
    combinedReport += '='.repeat(80) + '\n';
    combinedReport += `Folder: ${folderName}\n`;
    combinedReport += `Generated: ${new Date().toLocaleString()}\n`;
    combinedReport += '='.repeat(80) + '\n\n';

    let successCount = 0;
    let failCount = 0;

    for (const report of allReports) {
      try {
        const response = await getReportDownloadUrl(report.path);
        if (response.url) {
          // Fetch the report content
          const reportResponse = await fetch(response.url);
          if (reportResponse.ok) {
            const content = await reportResponse.text();
            combinedReport += '\n\n';
            combinedReport += '#'.repeat(80) + '\n';
            combinedReport += `# ${report.name}\n`;
            combinedReport += '#'.repeat(80) + '\n\n';
            combinedReport += content;
            successCount++;
          } else {
            combinedReport += `\n\n[${report.name}]: Report not available\n`;
            failCount++;
          }
        } else {
          combinedReport += `\n\n[${report.name}]: Report not found\n`;
          failCount++;
        }
      } catch (err) {
        combinedReport += `\n\n[${report.name}]: Error fetching report - ${err.message}\n`;
        failCount++;
      }
    }

    combinedReport += '\n\n';
    combinedReport += '='.repeat(80) + '\n';
    combinedReport += 'END OF FULL REPORT\n';
    combinedReport += `Reports included: ${successCount} | Reports missing: ${failCount}\n`;
    combinedReport += '='.repeat(80) + '\n';

    // Create and download the combined report
    const blob = new Blob([combinedReport], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${folderName}_Full_Report.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
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
                  onDownloadReport={handleDownloadReport}
                  folderName={getFolderName()}
                />
              ))}

              {/* Download Full Report Button */}
              {getFolderName() && (processInfo.status === 'SUCCEEDED' || processInfo.status === 'FAILED' || !isRunning) && (
                <div style={{
                  marginTop: '24px',
                  padding: '20px',
                  backgroundColor: COLORS.bgLight,
                  borderRadius: '12px',
                  border: `1px solid ${COLORS.border}`,
                  textAlign: 'center'
                }}>
                  <button
                    onClick={() => handleDownloadFullReport(getFolderName())}
                    style={{
                      padding: '14px 32px',
                      backgroundColor: COLORS.primary,
                      color: 'white',
                      border: 'none',
                      borderRadius: '8px',
                      fontSize: '15px',
                      fontWeight: '600',
                      cursor: 'pointer',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '8px'
                    }}
                  >
                    <span>📋</span>
                    Download Full Report
                  </button>
                  <div style={{ marginTop: '8px', fontSize: '13px', color: COLORS.textSecondary }}>
                    Combines all reports into a single document
                  </div>
                </div>
              )}
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
