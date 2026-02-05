import React, { useState, useEffect, useRef, useCallback } from 'react';
import { startPipeline, getPipelineStatus } from '../services/api';

// Pipeline steps with their sub-tasks
const PIPELINE_STEPS = [
  {
    id: 'sftp_download',
    label: 'SFTP Download',
    description: 'Download files from Sterling SFTP',
    subTasks: [
      { id: 'connect', label: 'Connect to SFTP server' },
      { id: 'list_files', label: 'List remote files' },
      { id: 'download_files', label: 'Download files', showFiles: true },
      { id: 'verify', label: 'Verify downloads' }
    ]
  },
  {
    id: 'create_folders',
    label: 'Create Folders',
    description: 'Create timestamped folder structure',
    subTasks: [
      { id: 'create_timestamp', label: 'Generate timestamp folder name' },
      { id: 'create_source', label: 'Create Source Files folder' },
      { id: 'create_delta', label: 'Create Delta Files folder' },
      { id: 'create_reports', label: 'Create Reports folder' },
      { id: 'move_files', label: 'Move files to Source Files' }
    ]
  },
  {
    id: 'validation',
    label: 'File Validation',
    description: 'Validate file names, duplicates, completeness',
    subTasks: [
      { id: 'check_duplicates', label: 'Check for duplicate files' },
      { id: 'validate_names', label: 'Validate file naming conventions' },
      { id: 'validate_schema', label: 'Validate file schemas' },
      { id: 'check_completeness', label: 'Check file completeness' },
      { id: 'generate_summary', label: 'Generate validation summary' }
    ]
  },
  {
    id: 'sql_load',
    label: 'SQL Load',
    description: 'Load files to SQL Server',
    subTasks: [
      { id: 'connect_db', label: 'Connect to SQL Server' },
      { id: 'drop_tables', label: 'Drop existing staging tables' },
      { id: 'create_tables', label: 'Create staging tables' },
      { id: 'load_data', label: 'Load CSV data', showFiles: true },
      { id: 'verify_counts', label: 'Verify row counts' }
    ]
  },
  {
    id: 'stored_procedure',
    label: 'Stored Procedure',
    description: 'Run HCM_MAIN_INTF (60+ min)',
    subTasks: [
      { id: 'start_proc', label: 'Start HCM_MAIN_INTF procedure' },
      { id: 'process_employees', label: 'Process employee records' },
      { id: 'process_assignments', label: 'Process assignments' },
      { id: 'process_salaries', label: 'Process salaries' },
      { id: 'generate_deltas', label: 'Generate delta records' },
      { id: 'complete', label: 'Procedure complete' }
    ]
  },
  {
    id: 'delta_export',
    label: 'Delta Export',
    description: 'Export delta files to S3',
    subTasks: [
      { id: 'query_deltas', label: 'Query delta tables' },
      { id: 'export_person', label: 'Export HCM_PERSON_INTF deltas' },
      { id: 'export_assignment', label: 'Export HCM_ASSIGNMENT_INTF deltas' },
      { id: 'export_salary', label: 'Export HCM_SALARY_INTF deltas' },
      { id: 'upload_s3', label: 'Upload to S3 Delta Files folder' }
    ]
  },
  {
    id: 'generate_report',
    label: 'Generate Report',
    description: 'Create final pipeline report',
    subTasks: [
      { id: 'collect_stats', label: 'Collect pipeline statistics' },
      { id: 'generate_summary', label: 'Generate execution summary' },
      { id: 'save_report', label: 'Save report to S3' }
    ]
  }
];

// Step status icons
const STATUS_ICONS = {
  pending: '○',
  running: '⏳',
  completed: '✓',
  failed: '✗'
};

// Color scheme matching desktop app
const COLORS = {
  primary: '#1a73e8',
  success: '#34a853',
  warning: '#fbbc04',
  error: '#ea4335',
  bgLight: '#f8f9fa',
  border: '#e0e0e0'
};

// Sub-task item component
const SubTaskItem = ({ subTask, status, files }) => (
  <div style={styles.subTaskItem}>
    <span style={{
      ...styles.subTaskIcon,
      color: status === 'completed' ? COLORS.success :
             status === 'running' ? COLORS.primary :
             status === 'failed' ? COLORS.error : '#ccc'
    }}>
      {STATUS_ICONS[status] || STATUS_ICONS.pending}
    </span>
    <div style={styles.subTaskContent}>
      <span style={{
        ...styles.subTaskLabel,
        color: status === 'completed' ? COLORS.success :
               status === 'running' ? '#333' : '#666'
      }}>
        {subTask.label}
      </span>
      {subTask.showFiles && files && files.length > 0 && (
        <div style={styles.filesList}>
          {files.map((file, idx) => (
            <div key={idx} style={styles.fileItem}>
              <span style={styles.fileIcon}>📄</span>
              <span style={styles.fileName}>{file.name || file}</span>
              {file.status && (
                <span style={{
                  ...styles.fileStatus,
                  color: file.status === 'completed' ? COLORS.success : COLORS.primary
                }}>
                  {file.status === 'completed' ? '✓' : '...'}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  </div>
);

// Expandable step component
const StepCard = ({ step, index, status, isActive, isExpanded, onToggle, stepDetails }) => {
  const subTaskStatuses = stepDetails?.subTasks || {};
  const files = stepDetails?.files || [];
  const stepProgress = stepDetails?.progress || 0;

  // Calculate sub-task progress
  const completedSubTasks = Object.values(subTaskStatuses).filter(s => s === 'completed').length;
  const totalSubTasks = step.subTasks.length;
  const calculatedProgress = status === 'completed' ? 100 :
                             status === 'running' ? Math.round((completedSubTasks / totalSubTasks) * 100) : 0;
  const displayProgress = stepProgress || calculatedProgress;

  return (
    <div style={{
      ...styles.stepCard,
      ...(isActive ? styles.stepCardActive : {}),
      ...(status === 'completed' ? styles.stepCardCompleted : {}),
      ...(status === 'failed' ? styles.stepCardFailed : {})
    }}>
      {/* Step Header - Clickable */}
      <div
        style={styles.stepHeader}
        onClick={onToggle}
      >
        <div style={styles.stepHeaderLeft}>
          <span style={{
            ...styles.stepIcon,
            color: status === 'completed' ? COLORS.success :
                   status === 'running' ? COLORS.primary :
                   status === 'failed' ? COLORS.error : '#999'
          }}>
            {STATUS_ICONS[status] || STATUS_ICONS.pending}
          </span>
          <div style={styles.stepInfo}>
            <span style={styles.stepLabel}>
              {index + 1}. {step.label}
            </span>
            <span style={styles.stepDescription}>{step.description}</span>
          </div>
        </div>
        <div style={styles.stepHeaderRight}>
          {(isActive || status === 'completed') && (
            <span style={styles.stepProgressText}>{displayProgress}%</span>
          )}
          <span style={{
            ...styles.expandIcon,
            transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)'
          }}>
            ▼
          </span>
        </div>
      </div>

      {/* Step Progress Bar (visible when active or completed) */}
      {(isActive || status === 'completed' || isExpanded) && (
        <div style={styles.stepProgressContainer}>
          <div style={styles.stepProgressBar}>
            <div style={{
              ...styles.stepProgressFill,
              width: `${displayProgress}%`,
              backgroundColor: status === 'failed' ? COLORS.error :
                               status === 'completed' ? COLORS.success : COLORS.primary
            }} />
          </div>
        </div>
      )}

      {/* Expanded Content */}
      {isExpanded && (
        <div style={styles.stepExpandedContent}>
          <div style={styles.subTasksList}>
            {step.subTasks.map((subTask, idx) => {
              const subStatus = subTaskStatuses[subTask.id] ||
                               (status === 'completed' ? 'completed' : 'pending');
              const subFiles = subTask.showFiles ? files : null;

              return (
                <SubTaskItem
                  key={subTask.id}
                  subTask={subTask}
                  status={subStatus}
                  files={subFiles}
                />
              );
            })}
          </div>

          {/* File count summary */}
          {files && files.length > 0 && (
            <div style={styles.fileSummary}>
              <span style={styles.fileSummaryText}>
                {files.filter(f => f.status === 'completed').length} of {files.length} files processed
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

function PipelineInterface() {
  // State
  const [isRunning, setIsRunning] = useState(false);
  const [executionId, setExecutionId] = useState(null);
  const [currentStep, setCurrentStep] = useState(null);
  const [stepStatuses, setStepStatuses] = useState({});
  const [stepDetails, setStepDetails] = useState({}); // Detailed info per step
  const [expandedSteps, setExpandedSteps] = useState({}); // Which steps are expanded
  const [progress, setProgress] = useState(0);
  const [statusMessage, setStatusMessage] = useState("Click 'Start Pipeline' to begin processing");
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null);
  const [elapsedTime, setElapsedTime] = useState(0);

  // Refs for intervals
  const timerRef = useRef(null);
  const pollingRef = useRef(null);
  const startTimeRef = useRef(null);

  // Format elapsed time as HH:MM:SS
  const formatTime = (seconds) => {
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    return `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  // Toggle step expansion
  const toggleStepExpansion = (stepId) => {
    setExpandedSteps(prev => ({
      ...prev,
      [stepId]: !prev[stepId]
    }));
  };

  // Auto-expand current step, collapse others
  useEffect(() => {
    if (currentStep) {
      setExpandedSteps(prev => {
        const newExpanded = {};
        PIPELINE_STEPS.forEach(step => {
          newExpanded[step.id] = step.id === currentStep;
        });
        return newExpanded;
      });
    }
  }, [currentStep]);

  // Calculate progress based on current step
  const calculateProgress = useCallback((currentStepId, isComplete = false) => {
    if (isComplete) return 100;
    if (!currentStepId) return 0;

    const stepIndex = PIPELINE_STEPS.findIndex(s => s.id === currentStepId);
    if (stepIndex === -1) return 0;

    const progressPerStep = 100 / PIPELINE_STEPS.length;
    return Math.min(Math.round((stepIndex + 0.5) * progressPerStep), 99);
  }, []);

  // Map Step Functions state names to our step IDs
  const mapStateToStepId = (stateName) => {
    const stateMap = {
      'SftpDownload': 'sftp_download',
      'CreateTimestampedFolder': 'create_folders',
      'RunValidation': 'validation',
      'CheckValidationPassed': 'validation',
      'SqlLoad': 'sql_load',
      'StartStoredProcedure': 'stored_procedure',
      'WaitForProcedure': 'stored_procedure',
      'CheckProcedureStatus': 'stored_procedure',
      'IsProcedureComplete': 'stored_procedure',
      'ExportDeltaFiles': 'delta_export',
      'GenerateFinalReport': 'generate_report',
      'PipelineSuccess': 'generate_report',
      'PipelineFailed': null,
      'PipelineFailedValidation': 'validation',
      'ProcedureFailed': 'stored_procedure',
      'GenerateValidationReport': 'validation'
    };
    return stateMap[stateName] || null;
  };

  // Update step details based on status response
  const updateStepDetails = useCallback((status) => {
    const newDetails = { ...stepDetails };

    // Parse step-specific details from the status response
    if (status.step_details) {
      Object.entries(status.step_details).forEach(([stepId, details]) => {
        newDetails[stepId] = {
          ...newDetails[stepId],
          ...details
        };
      });
    }

    // Simulate sub-task progress based on current state
    const currentStepId = mapStateToStepId(status.current_state);
    if (currentStepId && status.status === 'RUNNING') {
      const step = PIPELINE_STEPS.find(s => s.id === currentStepId);
      if (step) {
        // Mark earlier sub-tasks as completed, current one as running
        const subTasks = {};
        const runningIdx = Math.floor(Math.random() * step.subTasks.length);
        step.subTasks.forEach((st, idx) => {
          if (idx < runningIdx) {
            subTasks[st.id] = 'completed';
          } else if (idx === runningIdx) {
            subTasks[st.id] = 'running';
          }
        });
        newDetails[currentStepId] = {
          ...newDetails[currentStepId],
          subTasks
        };
      }
    }

    // Mark completed steps with all sub-tasks completed
    if (status.completed_states) {
      status.completed_states.forEach(state => {
        const stepId = mapStateToStepId(state);
        if (stepId) {
          const step = PIPELINE_STEPS.find(s => s.id === stepId);
          if (step) {
            const subTasks = {};
            step.subTasks.forEach(st => {
              subTasks[st.id] = 'completed';
            });
            newDetails[stepId] = {
              ...newDetails[stepId],
              subTasks,
              progress: 100
            };
          }
        }
      });
    }

    setStepDetails(newDetails);
  }, [stepDetails]);

  // Poll for status updates
  const pollStatus = useCallback(async () => {
    if (!executionId) return;

    try {
      const status = await getPipelineStatus(executionId);

      // Update step statuses based on response
      const newStatuses = { ...stepStatuses };
      const currentStepId = mapStateToStepId(status.current_state);

      // Mark completed steps
      if (status.completed_states) {
        status.completed_states.forEach(state => {
          const stepId = mapStateToStepId(state);
          if (stepId) {
            newStatuses[stepId] = 'completed';
          }
        });
      }

      // Mark current step as running
      if (currentStepId && status.status === 'RUNNING') {
        newStatuses[currentStepId] = 'running';
        setCurrentStep(currentStepId);
      }

      setStepStatuses(newStatuses);
      updateStepDetails(status);

      // Update progress
      const newProgress = calculateProgress(currentStepId, status.status === 'SUCCEEDED');
      setProgress(newProgress);

      // Update status message
      if (status.status === 'RUNNING') {
        const stepLabel = PIPELINE_STEPS.find(s => s.id === currentStepId)?.label || status.current_state;
        setStatusMessage(`Running: ${stepLabel}...`);
      } else if (status.status === 'SUCCEEDED') {
        setStatusMessage('Pipeline completed successfully!');
        setProgress(100);
        stopPipeline(status);
      } else if (status.status === 'FAILED' || status.status === 'TIMED_OUT' || status.status === 'ABORTED') {
        setError(status.error || `Pipeline ${status.status.toLowerCase()}`);
        stopPipeline(status);
      }

      // Store results
      if (status.output) {
        setResults(status.output);
      }

    } catch (err) {
      console.error('Error polling status:', err);
    }
  }, [executionId, stepStatuses, calculateProgress, updateStepDetails]);

  // Start the pipeline
  const handleStartPipeline = async () => {
    // Reset state
    setIsRunning(true);
    setError(null);
    setResults(null);
    setProgress(0);
    setStepStatuses({});
    setStepDetails({});
    setExpandedSteps({});
    setCurrentStep(null);
    setElapsedTime(0);
    setStatusMessage('Starting pipeline...');

    try {
      const response = await startPipeline({
        test_execution: false
      });

      if (response.execution_id) {
        setExecutionId(response.execution_id);
        setStatusMessage('Pipeline started. Monitoring progress...');

        startTimeRef.current = Date.now();
        timerRef.current = setInterval(() => {
          setElapsedTime(Math.floor((Date.now() - startTimeRef.current) / 1000));
        }, 1000);

        pollingRef.current = setInterval(pollStatus, 15000);
        setTimeout(pollStatus, 5000);
      } else {
        throw new Error(response.error || 'Failed to start pipeline');
      }
    } catch (err) {
      setError(err.message);
      setIsRunning(false);
      setStatusMessage('Failed to start pipeline');
    }
  };

  // Stop pipeline monitoring
  const stopPipeline = (finalStatus) => {
    setIsRunning(false);

    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }

    if (finalStatus?.status === 'SUCCEEDED') {
      const allCompleted = {};
      const allDetails = {};
      PIPELINE_STEPS.forEach(step => {
        allCompleted[step.id] = 'completed';
        const subTasks = {};
        step.subTasks.forEach(st => {
          subTasks[st.id] = 'completed';
        });
        allDetails[step.id] = { subTasks, progress: 100 };
      });
      setStepStatuses(allCompleted);
      setStepDetails(allDetails);
    }
  };

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  // Effect to restart polling when executionId changes
  useEffect(() => {
    if (executionId && isRunning && !pollingRef.current) {
      pollingRef.current = setInterval(pollStatus, 15000);
    }
  }, [executionId, isRunning, pollStatus]);

  // Download report as text file
  const handleDownloadReport = () => {
    if (!results) return;

    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const report = generateReport();

    const blob = new Blob([report], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `HaciendaERP_Pipeline_Report_${timestamp}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // Generate text report
  const generateReport = () => {
    const lines = [
      '='.repeat(80),
      'HACIENDA ERP DATA PIPELINE REPORT',
      '='.repeat(80),
      '',
      `Generated: ${new Date().toLocaleString()}`,
      `Elapsed Time: ${formatTime(elapsedTime)}`,
      `Status: ${error ? 'FAILED' : 'SUCCESS'}`,
      '',
      '-'.repeat(80),
      'PIPELINE STEPS',
      '-'.repeat(80),
      ''
    ];

    PIPELINE_STEPS.forEach((step, idx) => {
      const status = stepStatuses[step.id] || 'pending';
      const icon = status === 'completed' ? '[OK]' : status === 'failed' ? '[FAIL]' : '[--]';
      lines.push(`${idx + 1}. ${icon} ${step.label}`);
      lines.push(`      ${step.description}`);

      // Add sub-task details
      const details = stepDetails[step.id];
      if (details?.subTasks) {
        step.subTasks.forEach(st => {
          const subStatus = details.subTasks[st.id] || 'pending';
          const subIcon = subStatus === 'completed' ? '✓' : subStatus === 'failed' ? '✗' : '-';
          lines.push(`        ${subIcon} ${st.label}`);
        });
      }
      lines.push('');
    });

    if (results) {
      lines.push('-'.repeat(80));
      lines.push('RESULTS');
      lines.push('-'.repeat(80));
      lines.push('');
      lines.push(JSON.stringify(results, null, 2));
    }

    if (error) {
      lines.push('-'.repeat(80));
      lines.push('ERROR');
      lines.push('-'.repeat(80));
      lines.push('');
      lines.push(error);
    }

    return lines.join('\n');
  };

  return (
    <div style={styles.container}>
      {/* Header */}
      <div style={styles.header}>
        <h2 style={styles.title}>Hacienda ERP Data Pipeline</h2>
        <p style={styles.subtitle}>Full HCM Processing Pipeline</p>
      </div>

      {/* Action Buttons */}
      <div style={styles.buttonRow}>
        <button
          onClick={handleStartPipeline}
          disabled={isRunning}
          style={{
            ...styles.startButton,
            ...(isRunning ? styles.buttonDisabled : {})
          }}
        >
          {isRunning ? '⏳ Pipeline Running...' : '▶ Start Pipeline'}
        </button>

        <button
          onClick={handleDownloadReport}
          disabled={!results && !error}
          style={{
            ...styles.reportButton,
            ...(!results && !error ? styles.buttonDisabled : {})
          }}
        >
          📄 Download Report
        </button>
      </div>

      {/* Main Progress Section */}
      <div style={styles.progressSection}>
        <div style={styles.timerRow}>
          <div style={styles.timerContainer}>
            <span style={styles.timerLabel}>Time Running:</span>
            <span style={styles.timerValue}>{formatTime(elapsedTime)}</span>
          </div>
          <span style={styles.percentValue}>{progress}%</span>
        </div>

        <div style={styles.progressBarContainer}>
          <div
            style={{
              ...styles.progressBarFill,
              width: `${progress}%`,
              backgroundColor: error ? COLORS.error : (progress === 100 ? COLORS.success : COLORS.primary)
            }}
          />
        </div>

        <p style={styles.statusMessage}>{statusMessage}</p>
      </div>

      {/* Steps Section */}
      <div style={styles.stepsSection}>
        <h3 style={styles.sectionTitle}>Pipeline Steps</h3>
        <div style={styles.stepsList}>
          {PIPELINE_STEPS.map((step, index) => (
            <StepCard
              key={step.id}
              step={step}
              index={index}
              status={stepStatuses[step.id] || 'pending'}
              isActive={currentStep === step.id}
              isExpanded={expandedSteps[step.id] || false}
              onToggle={() => toggleStepExpansion(step.id)}
              stepDetails={stepDetails[step.id] || {}}
            />
          ))}
        </div>
      </div>

      {/* Error Display */}
      {error && (
        <div style={styles.errorSection}>
          <h3 style={styles.errorTitle}>Error</h3>
          <p style={styles.errorMessage}>{error}</p>
        </div>
      )}

      {/* Results Display */}
      {results && !error && (
        <div style={styles.resultsSection}>
          <h3 style={styles.sectionTitle}>Results</h3>
          <pre style={styles.resultsContent}>
            {JSON.stringify(results, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

// Styles
const styles = {
  container: {
    maxWidth: '900px',
    margin: '0 auto',
    padding: '20px'
  },
  header: {
    textAlign: 'center',
    marginBottom: '24px'
  },
  title: {
    margin: '0 0 8px 0',
    fontSize: '28px',
    color: '#232f3e'
  },
  subtitle: {
    margin: 0,
    color: '#666',
    fontSize: '14px'
  },
  buttonRow: {
    display: 'flex',
    gap: '16px',
    justifyContent: 'center',
    marginBottom: '24px'
  },
  startButton: {
    padding: '14px 32px',
    fontSize: '16px',
    fontWeight: 'bold',
    backgroundColor: COLORS.success,
    color: 'white',
    border: 'none',
    borderRadius: '8px',
    cursor: 'pointer',
    transition: 'background-color 0.2s'
  },
  reportButton: {
    padding: '14px 32px',
    fontSize: '16px',
    fontWeight: 'bold',
    backgroundColor: COLORS.primary,
    color: 'white',
    border: 'none',
    borderRadius: '8px',
    cursor: 'pointer',
    transition: 'background-color 0.2s'
  },
  buttonDisabled: {
    backgroundColor: '#ccc',
    cursor: 'not-allowed'
  },
  progressSection: {
    backgroundColor: COLORS.bgLight,
    borderRadius: '8px',
    padding: '20px',
    marginBottom: '24px'
  },
  timerRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: '12px'
  },
  timerContainer: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px'
  },
  timerLabel: {
    color: '#666',
    fontSize: '14px'
  },
  timerValue: {
    fontFamily: 'Consolas, monospace',
    fontSize: '20px',
    fontWeight: 'bold',
    color: COLORS.primary
  },
  percentValue: {
    fontSize: '20px',
    fontWeight: 'bold',
    color: '#333'
  },
  progressBarContainer: {
    height: '12px',
    backgroundColor: '#e0e0e0',
    borderRadius: '6px',
    overflow: 'hidden',
    marginBottom: '12px'
  },
  progressBarFill: {
    height: '100%',
    borderRadius: '6px',
    transition: 'width 0.5s ease-in-out'
  },
  statusMessage: {
    margin: 0,
    color: '#666',
    fontSize: '14px'
  },
  stepsSection: {
    backgroundColor: '#fff',
    border: `1px solid ${COLORS.border}`,
    borderRadius: '8px',
    padding: '20px',
    marginBottom: '24px'
  },
  sectionTitle: {
    margin: '0 0 16px 0',
    fontSize: '18px',
    color: '#232f3e'
  },
  stepsList: {
    display: 'flex',
    flexDirection: 'column',
    gap: '12px'
  },
  // Step Card styles
  stepCard: {
    backgroundColor: COLORS.bgLight,
    borderRadius: '8px',
    border: `1px solid ${COLORS.border}`,
    overflow: 'hidden',
    transition: 'all 0.3s ease'
  },
  stepCardActive: {
    backgroundColor: '#e8f0fe',
    borderColor: COLORS.primary,
    boxShadow: '0 2px 8px rgba(26, 115, 232, 0.2)'
  },
  stepCardCompleted: {
    backgroundColor: '#e6f4ea',
    borderColor: '#a8dab5'
  },
  stepCardFailed: {
    backgroundColor: '#fce8e6',
    borderColor: '#f5c6cb'
  },
  stepHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '16px',
    cursor: 'pointer',
    userSelect: 'none'
  },
  stepHeaderLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px'
  },
  stepHeaderRight: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px'
  },
  stepIcon: {
    fontSize: '20px',
    width: '28px',
    height: '28px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center'
  },
  stepInfo: {
    display: 'flex',
    flexDirection: 'column'
  },
  stepLabel: {
    fontWeight: '600',
    fontSize: '15px',
    color: '#333'
  },
  stepDescription: {
    fontSize: '13px',
    color: '#666',
    marginTop: '2px'
  },
  stepProgressText: {
    fontSize: '14px',
    fontWeight: '600',
    color: COLORS.primary
  },
  expandIcon: {
    fontSize: '12px',
    color: '#666',
    transition: 'transform 0.3s ease'
  },
  stepProgressContainer: {
    padding: '0 16px 12px 16px'
  },
  stepProgressBar: {
    height: '6px',
    backgroundColor: '#e0e0e0',
    borderRadius: '3px',
    overflow: 'hidden'
  },
  stepProgressFill: {
    height: '100%',
    borderRadius: '3px',
    transition: 'width 0.5s ease-in-out'
  },
  stepExpandedContent: {
    padding: '0 16px 16px 16px',
    borderTop: `1px solid ${COLORS.border}`,
    marginTop: '0'
  },
  subTasksList: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
    marginTop: '12px'
  },
  subTaskItem: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: '10px',
    padding: '8px 12px',
    backgroundColor: 'rgba(255,255,255,0.7)',
    borderRadius: '6px'
  },
  subTaskIcon: {
    fontSize: '14px',
    width: '20px',
    height: '20px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0
  },
  subTaskContent: {
    flex: 1
  },
  subTaskLabel: {
    fontSize: '13px',
    fontWeight: '500'
  },
  filesList: {
    marginTop: '8px',
    paddingLeft: '8px',
    borderLeft: '2px solid #e0e0e0'
  },
  fileItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    padding: '4px 0',
    fontSize: '12px'
  },
  fileIcon: {
    fontSize: '12px'
  },
  fileName: {
    color: '#555',
    fontFamily: 'Consolas, monospace'
  },
  fileStatus: {
    marginLeft: 'auto',
    fontWeight: '600'
  },
  fileSummary: {
    marginTop: '12px',
    padding: '8px 12px',
    backgroundColor: 'rgba(255,255,255,0.9)',
    borderRadius: '6px',
    textAlign: 'center'
  },
  fileSummaryText: {
    fontSize: '12px',
    color: '#666',
    fontWeight: '500'
  },
  errorSection: {
    backgroundColor: '#fce8e6',
    border: '1px solid #f5c6cb',
    borderRadius: '8px',
    padding: '20px',
    marginBottom: '24px'
  },
  errorTitle: {
    margin: '0 0 12px 0',
    color: COLORS.error,
    fontSize: '18px'
  },
  errorMessage: {
    margin: 0,
    color: '#721c24',
    whiteSpace: 'pre-wrap'
  },
  resultsSection: {
    backgroundColor: '#fff',
    border: `1px solid ${COLORS.border}`,
    borderRadius: '8px',
    padding: '20px'
  },
  resultsContent: {
    margin: 0,
    padding: '16px',
    backgroundColor: COLORS.bgLight,
    borderRadius: '6px',
    fontSize: '13px',
    overflow: 'auto',
    maxHeight: '300px',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word'
  }
};

export default PipelineInterface;
