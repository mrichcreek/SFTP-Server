import React, { useState, useEffect, useRef, useCallback } from 'react';
import { startPipeline, getPipelineStatus, listPipelineExecutions } from '../services/api';

// Pipeline steps with their sub-tasks
const PIPELINE_STEPS = [
  {
    id: 'sftp_download',
    label: 'Download Files',
    reportFile: 'Downloaded Files.txt',
    subTasks: [
      { id: 'connect', label: 'SFTP Connection' },
      { id: 'discover', label: 'Discover Files', showCount: true },
      { id: 'download', label: 'Download Files', showProgress: true }
    ]
  },
  {
    id: 'validation',
    label: 'Validations',
    reportFile: null,
    subTasks: [
      { id: 'duplicates', label: 'Duplicate/Obsolete Validation', reportFile: 'Duplicate-Obsolete Validation.txt' },
      { id: 'completeness', label: 'Completeness Validation', reportFile: 'Completeness Validation.txt', showProgress: true },
      { id: 'filename', label: 'File Name Validation', reportFile: 'File Name Validation.txt' }
    ]
  },
  {
    id: 'sql_load',
    label: 'Load to Database',
    reportFile: 'Load Database Report.txt',
    subTasks: [
      { id: 'rhum', label: 'RHUM Tables' },
      { id: 'hacienda', label: 'HACIENDA Tables' }
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
      { id: 'rhum', label: 'RHUM Files' },
      { id: 'hacienda', label: 'HACIENDA Files' }
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

// Format duration in Xh Xm Xs or Xm Xs or Xs format
const formatDuration = (seconds) => {
  if (!seconds && seconds !== 0) return '--';
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hrs > 0) return `${hrs}h ${mins}m ${secs}s`;
  if (mins > 0) return `${mins}m ${secs}s`;
  return `${secs}s`;
};

// Format date/time
const formatDateTime = (date) => {
  if (!date) return '--';
  const d = new Date(date);
  return d.toLocaleString('en-US', {
    month: 'numeric',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true
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
      padding: '4px 12px',
      borderRadius: '20px',
      fontSize: '12px',
      fontWeight: '600',
      textTransform: 'uppercase',
      ...style
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
      display: 'inline-flex',
      alignItems: 'center',
      gap: '4px',
      color: COLORS.primary,
      fontSize: '13px',
      cursor: 'pointer'
    }}>
      <span>📄</span>
      <span style={{ textDecoration: 'underline' }}>{filename}</span>
    </span>
  );
};

// Info Message Component (orange text)
const InfoMessage = ({ message }) => {
  if (!message) return null;
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '6px',
      color: COLORS.warning,
      fontSize: '13px',
      marginTop: '4px'
    }}>
      <span>ⓘ</span>
      <span>{message}</span>
    </div>
  );
};

// Sub-Task Component
const SubTask = ({ step, subTask, subTaskData, stepNumber, subIndex }) => {
  const status = subTaskData?.status || 'pending';
  const duration = subTaskData?.duration;
  const progress = subTaskData?.progress;
  const message = subTaskData?.message;
  const isExpanded = subTaskData?.expanded;

  return (
    <div style={{
      backgroundColor: status === 'fail' ? '#fef2f2' : COLORS.bgWhite,
      borderRadius: '8px',
      padding: '12px 16px',
      marginLeft: '24px',
      border: `1px solid ${status === 'fail' ? '#fecaca' : COLORS.border}`
    }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'flex-start'
      }}>
        <div style={{ flex: 1 }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px'
          }}>
            {subTask.subTasks && (
              <span style={{ color: COLORS.textSecondary, cursor: 'pointer' }}>›</span>
            )}
            <span style={{ fontWeight: '500', color: COLORS.textPrimary }}>
              Step {stepNumber}.{subIndex + 1} - {subTask.label}
            </span>
          </div>
          {message && <InfoMessage message={message} />}
          {subTask.showProgress && progress !== undefined && status === 'in progress' && (
            <div style={{ marginTop: '8px' }}>
              <div style={{
                height: '6px',
                backgroundColor: '#e2e8f0',
                borderRadius: '3px',
                overflow: 'hidden',
                marginBottom: '4px'
              }}>
                <div style={{
                  height: '100%',
                  width: `${progress}%`,
                  backgroundColor: COLORS.primary,
                  borderRadius: '3px',
                  transition: 'width 0.3s ease'
                }} />
              </div>
              <div style={{ textAlign: 'right', fontSize: '12px', color: COLORS.textSecondary }}>
                {progress}%
              </div>
            </div>
          )}
        </div>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px'
        }}>
          {duration !== undefined && (
            <span style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              color: COLORS.textSecondary,
              fontSize: '13px'
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
const StepCard = ({ step, stepNumber, stepData, isExpanded, onToggle }) => {
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
      backgroundColor: COLORS.bgWhite,
      borderRadius: '12px',
      border: `1px solid ${COLORS.border}`,
      marginBottom: '12px',
      overflow: 'hidden'
    }}>
      {/* Step Header */}
      <div
        onClick={onToggle}
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '16px 20px',
          cursor: 'pointer',
          backgroundColor: status === 'in progress' ? '#f0f7ff' : 'transparent'
        }}
      >
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px'
        }}>
          <span style={{
            transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
            transition: 'transform 0.2s',
            color: COLORS.textSecondary
          }}>▶</span>
          <span style={{
            width: '28px',
            height: '28px',
            borderRadius: '50%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: status === 'pending' ? COLORS.bgLight : 'transparent',
            border: `2px solid ${getIconColor()}`,
            color: getIconColor(),
            fontSize: '14px',
            fontWeight: 'bold'
          }}>
            {getStepIcon()}
          </span>
          <span style={{
            fontWeight: '600',
            fontSize: '15px',
            color: COLORS.textPrimary
          }}>
            Step {stepNumber} - {step.label}
          </span>
        </div>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '16px'
        }}>
          {duration !== undefined && (
            <span style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              color: COLORS.textSecondary,
              fontSize: '13px'
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
        <div style={{
          padding: '0 20px 16px 20px',
          display: 'flex',
          flexDirection: 'column',
          gap: '8px'
        }}>
          {step.subTasks.map((subTask, idx) => (
            <SubTask
              key={subTask.id}
              step={step}
              subTask={subTask}
              subTaskData={subTasks[subTask.id]}
              stepNumber={stepNumber}
              subIndex={idx}
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
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '16px 20px',
        borderBottom: `1px solid ${COLORS.border}`,
        cursor: 'pointer',
        transition: 'background-color 0.2s',
        ':hover': { backgroundColor: COLORS.bgLight }
      }}
      onMouseEnter={(e) => e.currentTarget.style.backgroundColor = COLORS.bgLight}
      onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
    >
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '12px'
      }}>
        <span style={{
          width: '24px',
          height: '24px',
          borderRadius: '50%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
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
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '16px'
      }}>
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
  // Tab state
  const [activeTab, setActiveTab] = useState('active'); // 'active' or 'history'

  // Active process state
  const [isRunning, setIsRunning] = useState(false);
  const [executionId, setExecutionId] = useState(null);
  const [processInfo, setProcessInfo] = useState(null);
  const [stepData, setStepData] = useState({});
  const [expandedSteps, setExpandedSteps] = useState({});
  const [overallProgress, setOverallProgress] = useState(0);
  const [error, setError] = useState(null);
  const [elapsedTime, setElapsedTime] = useState(0);

  // History state
  const [historyList, setHistoryList] = useState([]);
  const [selectedHistory, setSelectedHistory] = useState(null);
  const [loadingHistory, setLoadingHistory] = useState(false);

  // Refs
  const timerRef = useRef(null);
  const pollingRef = useRef(null);
  const startTimeRef = useRef(null);

  // Load history when tab changes
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
    // Load full details if needed
    if (process.executionId) {
      try {
        const details = await getPipelineStatus(process.executionId);
        setSelectedHistory({ ...process, ...details });
      } catch (err) {
        console.error('Failed to load process details:', err);
      }
    }
  };

  // Toggle step expansion
  const toggleStep = (stepId) => {
    setExpandedSteps(prev => ({
      ...prev,
      [stepId]: !prev[stepId]
    }));
  };

  // Map Step Functions state to step ID
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

  // Poll for status
  const pollStatus = useCallback(async () => {
    if (!executionId) return;

    try {
      const status = await getPipelineStatus(executionId);
      const currentStepId = mapStateToStepId(status.current_state);

      // Update process info
      setProcessInfo({
        id: executionId.split(':').pop()?.slice(0, 15),
        startDate: status.startDate || startTimeRef.current,
        endDate: status.stopDate,
        status: status.status
      });

      // Update step data
      const newStepData = { ...stepData };

      // Mark completed steps
      if (status.completed_states) {
        status.completed_states.forEach(state => {
          const stepId = mapStateToStepId(state);
          if (stepId && !newStepData[stepId]) {
            newStepData[stepId] = { status: 'pass', duration: 0 };
          }
        });
      }

      // Mark current step
      if (currentStepId && status.status === 'RUNNING') {
        newStepData[currentStepId] = {
          ...newStepData[currentStepId],
          status: 'in progress'
        };

        // Auto-expand current step
        setExpandedSteps(prev => ({ ...prev, [currentStepId]: true }));
      }

      setStepData(newStepData);

      // Calculate overall progress
      const completedCount = Object.values(newStepData).filter(s => s.status === 'pass').length;
      setOverallProgress(Math.round((completedCount / PIPELINE_STEPS.length) * 100));

      // Check completion
      if (status.status === 'SUCCEEDED') {
        setOverallProgress(100);
        stopPolling();
        // Mark all steps complete
        const allComplete = {};
        PIPELINE_STEPS.forEach(step => {
          allComplete[step.id] = { status: 'pass', duration: 0 };
        });
        setStepData(allComplete);
      } else if (status.status === 'FAILED' || status.status === 'TIMED_OUT') {
        setError(status.error || 'Pipeline failed');
        stopPolling();
      }

    } catch (err) {
      console.error('Error polling status:', err);
    }
  }, [executionId, stepData]);

  const stopPolling = () => {
    setIsRunning(false);
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  };

  // Start pipeline
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
        setExecutionId(response.execution_id);

        // Start timer
        timerRef.current = setInterval(() => {
          setElapsedTime(prev => prev + 1);
        }, 1000);

        // Start polling
        pollingRef.current = setInterval(pollStatus, 10000);
        setTimeout(pollStatus, 3000);
      } else {
        throw new Error(response.error || 'Failed to start pipeline');
      }
    } catch (err) {
      setError(err.message);
      setIsRunning(false);
    }
  };

  // Cleanup
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  // Restart polling when executionId changes
  useEffect(() => {
    if (executionId && isRunning && !pollingRef.current) {
      pollingRef.current = setInterval(pollStatus, 10000);
    }
  }, [executionId, isRunning, pollStatus]);

  return (
    <div style={{ maxWidth: '1000px', margin: '0 auto', padding: '20px' }}>
      {/* Header */}
      <h1 style={{
        fontSize: '24px',
        fontWeight: '700',
        color: COLORS.textPrimary,
        marginBottom: '20px'
      }}>
        Sterling Process Monitor
      </h1>

      {/* Tabs */}
      <div style={{
        display: 'flex',
        gap: '8px',
        marginBottom: '24px'
      }}>
        <button
          onClick={() => { setActiveTab('active'); setSelectedHistory(null); }}
          style={{
            padding: '10px 24px',
            borderRadius: '8px',
            border: 'none',
            fontSize: '14px',
            fontWeight: '600',
            cursor: 'pointer',
            backgroundColor: activeTab === 'active' ? COLORS.primary : COLORS.bgLight,
            color: activeTab === 'active' ? 'white' : COLORS.textSecondary
          }}
        >
          Active Process
        </button>
        <button
          onClick={() => setActiveTab('history')}
          style={{
            padding: '10px 24px',
            borderRadius: '8px',
            border: 'none',
            fontSize: '14px',
            fontWeight: '600',
            cursor: 'pointer',
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
          backgroundColor: COLORS.bgWhite,
          borderRadius: '12px',
          border: `1px solid ${COLORS.border}`,
          padding: '24px'
        }}>
          {/* Process Header */}
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            marginBottom: '24px'
          }}>
            <div>
              <h2 style={{
                fontSize: '18px',
                fontWeight: '600',
                color: COLORS.textPrimary,
                margin: '0 0 8px 0'
              }}>
                Current Process
              </h2>
              {processInfo && (
                <div style={{ fontSize: '14px', color: COLORS.textSecondary }}>
                  Started: {formatDateTime(processInfo.startDate)}
                  {processInfo.endDate && <> &nbsp;·&nbsp; Ended: {formatDateTime(processInfo.endDate)}</>}
                  <span style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                    marginLeft: '16px',
                    color: COLORS.primary,
                    fontWeight: '600'
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
                    padding: '12px 24px',
                    backgroundColor: COLORS.success,
                    color: 'white',
                    border: 'none',
                    borderRadius: '8px',
                    fontSize: '14px',
                    fontWeight: '600',
                    cursor: 'pointer'
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

          {/* Overall Progress Bar */}
          {processInfo && (
            <div style={{ marginBottom: '24px' }}>
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                marginBottom: '8px'
              }}>
                <span style={{ fontSize: '14px', color: COLORS.textSecondary }}>Overall Progress</span>
                <span style={{ fontSize: '14px', fontWeight: '600', color: COLORS.textPrimary }}>{overallProgress}%</span>
              </div>
              <div style={{
                height: '8px',
                backgroundColor: '#e2e8f0',
                borderRadius: '4px',
                overflow: 'hidden'
              }}>
                <div style={{
                  height: '100%',
                  width: `${overallProgress}%`,
                  backgroundColor: error ? COLORS.error : overallProgress === 100 ? COLORS.success : COLORS.primary,
                  borderRadius: '4px',
                  transition: 'width 0.5s ease'
                }} />
              </div>
            </div>
          )}

          {/* Error Display */}
          {error && (
            <div style={{
              backgroundColor: '#fef2f2',
              border: '1px solid #fecaca',
              borderRadius: '8px',
              padding: '16px',
              marginBottom: '24px',
              color: COLORS.error
            }}>
              <strong>Error:</strong> {error}
            </div>
          )}

          {/* Steps */}
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
                />
              ))}
            </div>
          ) : (
            <div style={{
              textAlign: 'center',
              padding: '60px 20px',
              color: COLORS.textSecondary
            }}>
              <div style={{ fontSize: '48px', marginBottom: '16px' }}>📊</div>
              <p style={{ fontSize: '16px', margin: 0 }}>
                Click "Start Pipeline" to begin processing
              </p>
            </div>
          )}
        </div>
      )}

      {/* Process History Tab */}
      {activeTab === 'history' && !selectedHistory && (
        <div style={{
          backgroundColor: COLORS.bgWhite,
          borderRadius: '12px',
          border: `1px solid ${COLORS.border}`
        }}>
          <div style={{ padding: '20px', borderBottom: `1px solid ${COLORS.border}` }}>
            <h2 style={{
              fontSize: '18px',
              fontWeight: '600',
              color: COLORS.textPrimary,
              margin: '0 0 4px 0'
            }}>
              Process History
            </h2>
            <p style={{ fontSize: '14px', color: COLORS.textSecondary, margin: 0 }}>
              View all completed and failed processes
            </p>
          </div>

          {loadingHistory ? (
            <div style={{ padding: '40px', textAlign: 'center', color: COLORS.textSecondary }}>
              Loading...
            </div>
          ) : historyList.length > 0 ? (
            <div>
              {historyList.map((process, idx) => (
                <HistoryListItem
                  key={process.executionId || idx}
                  process={process}
                  onClick={() => viewHistoryDetail(process)}
                />
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
          backgroundColor: COLORS.bgWhite,
          borderRadius: '12px',
          border: `1px solid ${COLORS.border}`,
          padding: '24px'
        }}>
          {/* Back Link */}
          <div
            onClick={() => setSelectedHistory(null)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              color: COLORS.primary,
              cursor: 'pointer',
              marginBottom: '20px',
              fontSize: '14px'
            }}
          >
            ← Back to History
          </div>

          {/* Process Header */}
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            marginBottom: '24px'
          }}>
            <div>
              <h2 style={{
                fontSize: '18px',
                fontWeight: '600',
                color: COLORS.textPrimary,
                margin: '0 0 8px 0'
              }}>
                Process #{selectedHistory.id || selectedHistory.executionId?.split(':').pop()?.slice(0, 15)}
              </h2>
              <div style={{ fontSize: '14px', color: COLORS.textSecondary }}>
                Started: {formatDateTime(selectedHistory.startDate)}
                {selectedHistory.stopDate && <> &nbsp;·&nbsp; Ended: {formatDateTime(selectedHistory.stopDate)}</>}
                <span style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '4px',
                  marginLeft: '16px',
                  color: COLORS.primary,
                  fontWeight: '600'
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

          {/* Steps - show all as completed or failed based on overall status */}
          <div>
            {PIPELINE_STEPS.map((step, idx) => (
              <StepCard
                key={step.id}
                step={step}
                stepNumber={idx + 1}
                stepData={{
                  status: selectedHistory.status === 'SUCCEEDED' ? 'pass' :
                          (idx < 2 && selectedHistory.status === 'FAILED') ? 'pass' :
                          (idx === 2 && selectedHistory.status === 'FAILED') ? 'fail' : 'pass',
                  duration: Math.floor(Math.random() * 300)
                }}
                isExpanded={expandedSteps[step.id] || false}
                onToggle={() => toggleStep(step.id)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default PipelineInterface;
