import React, { useState, useEffect, useRef, useCallback } from 'react';
import { startPipeline, getPipelineStatus } from '../services/api';

// Pipeline steps matching the Step Functions state machine
const PIPELINE_STEPS = [
  { id: 'sftp_download', label: 'SFTP Download', description: 'Download files from Sterling SFTP' },
  { id: 'create_folders', label: 'Create Folders', description: 'Create timestamped folder structure' },
  { id: 'validation', label: 'File Validation', description: 'Validate file names, duplicates, completeness' },
  { id: 'sql_load', label: 'SQL Load', description: 'Load files to SQL Server' },
  { id: 'stored_procedure', label: 'Stored Procedure', description: 'Run HCM_MAIN_INTF (60+ min)' },
  { id: 'delta_export', label: 'Delta Export', description: 'Export delta files to S3' },
  { id: 'sftp_upload', label: 'SFTP Upload', description: 'Upload files to Sterling' },
  { id: 'generate_report', label: 'Generate Report', description: 'Create final pipeline report' }
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
  bgDark: '#202124',
  bgMedium: '#303134',
  bgLight: '#3c4043',
  textPrimary: '#e8eaed',
  textSecondary: '#9aa0a6',
  border: '#5f6368'
};

function PipelineInterface() {
  // State
  const [isRunning, setIsRunning] = useState(false);
  const [executionId, setExecutionId] = useState(null);
  const [currentStep, setCurrentStep] = useState(null);
  const [stepStatuses, setStepStatuses] = useState({});
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

  // Calculate progress based on current step
  const calculateProgress = useCallback((currentStepId, isComplete = false) => {
    if (isComplete) return 100;
    if (!currentStepId) return 0;

    const stepIndex = PIPELINE_STEPS.findIndex(s => s.id === currentStepId);
    if (stepIndex === -1) return 0;

    // Each step is roughly equal progress
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
      'SftpUpload': 'sftp_upload',
      'GenerateFinalReport': 'generate_report',
      'PipelineSuccess': 'generate_report',
      'PipelineFailed': null,
      'PipelineFailedValidation': 'validation',
      'ProcedureFailed': 'stored_procedure',
      'GenerateValidationReport': 'validation'
    };
    return stateMap[stateName] || null;
  };

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
      // Don't stop polling on transient errors
    }
  }, [executionId, stepStatuses, calculateProgress]);

  // Start the pipeline
  const handleStartPipeline = async () => {
    // Reset state
    setIsRunning(true);
    setError(null);
    setResults(null);
    setProgress(0);
    setStepStatuses({});
    setCurrentStep(null);
    setElapsedTime(0);
    setStatusMessage('Starting pipeline...');

    try {
      // Start Step Functions execution
      const response = await startPipeline({
        test_execution: false // Production mode
      });

      if (response.execution_id) {
        setExecutionId(response.execution_id);
        setStatusMessage('Pipeline started. Monitoring progress...');

        // Start timer
        startTimeRef.current = Date.now();
        timerRef.current = setInterval(() => {
          setElapsedTime(Math.floor((Date.now() - startTimeRef.current) / 1000));
        }, 1000);

        // Start polling for status (every 15 seconds to avoid rate limits)
        pollingRef.current = setInterval(pollStatus, 15000);
        // Initial poll after 5 seconds
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

    // Stop intervals
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }

    // Update all steps to completed or failed based on final status
    if (finalStatus?.status === 'SUCCEEDED') {
      const allCompleted = {};
      PIPELINE_STEPS.forEach(step => {
        allCompleted[step.id] = 'completed';
      });
      setStepStatuses(allCompleted);
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
      '=' .repeat(80),
      'HACIENDA ERP DATA PIPELINE REPORT',
      '=' .repeat(80),
      '',
      `Generated: ${new Date().toLocaleString()}`,
      `Elapsed Time: ${formatTime(elapsedTime)}`,
      `Status: ${error ? 'FAILED' : 'SUCCESS'}`,
      '',
      '-' .repeat(80),
      'PIPELINE STEPS',
      '-' .repeat(80),
      ''
    ];

    PIPELINE_STEPS.forEach((step, idx) => {
      const status = stepStatuses[step.id] || 'pending';
      const icon = status === 'completed' ? '[OK]' : status === 'failed' ? '[FAIL]' : '[--]';
      lines.push(`${idx + 1}. ${icon} ${step.label}`);
      lines.push(`      ${step.description}`);
    });

    if (results) {
      lines.push('');
      lines.push('-' .repeat(80));
      lines.push('RESULTS');
      lines.push('-' .repeat(80));
      lines.push('');
      lines.push(JSON.stringify(results, null, 2));
    }

    if (error) {
      lines.push('');
      lines.push('-' .repeat(80));
      lines.push('ERROR');
      lines.push('-' .repeat(80));
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

      {/* Progress Section */}
      <div style={styles.progressSection}>
        {/* Timer and Percentage */}
        <div style={styles.timerRow}>
          <div style={styles.timerContainer}>
            <span style={styles.timerLabel}>Time Running:</span>
            <span style={styles.timerValue}>{formatTime(elapsedTime)}</span>
          </div>
          <span style={styles.percentValue}>{progress}%</span>
        </div>

        {/* Progress Bar */}
        <div style={styles.progressBarContainer}>
          <div
            style={{
              ...styles.progressBarFill,
              width: `${progress}%`,
              backgroundColor: error ? COLORS.error : (progress === 100 ? COLORS.success : COLORS.primary)
            }}
          />
        </div>

        {/* Status Message */}
        <p style={styles.statusMessage}>{statusMessage}</p>
      </div>

      {/* Steps Checklist */}
      <div style={styles.stepsSection}>
        <h3 style={styles.sectionTitle}>Pipeline Steps</h3>
        <div style={styles.stepsList}>
          {PIPELINE_STEPS.map((step, index) => {
            const status = stepStatuses[step.id] || 'pending';
            const isActive = currentStep === step.id;

            return (
              <div
                key={step.id}
                style={{
                  ...styles.stepItem,
                  ...(isActive ? styles.stepItemActive : {}),
                  ...(status === 'completed' ? styles.stepItemCompleted : {}),
                  ...(status === 'failed' ? styles.stepItemFailed : {})
                }}
              >
                <span style={{
                  ...styles.stepIcon,
                  ...(status === 'completed' ? styles.stepIconCompleted : {}),
                  ...(status === 'running' ? styles.stepIconRunning : {}),
                  ...(status === 'failed' ? styles.stepIconFailed : {})
                }}>
                  {STATUS_ICONS[status]}
                </span>
                <div style={styles.stepContent}>
                  <span style={styles.stepLabel}>
                    {index + 1}. {step.label}
                  </span>
                  <span style={styles.stepDescription}>{step.description}</span>
                </div>
              </div>
            );
          })}
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
    maxWidth: '800px',
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
    backgroundColor: '#f8f9fa',
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
    border: '1px solid #e0e0e0',
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
    gap: '8px'
  },
  stepItem: {
    display: 'flex',
    alignItems: 'flex-start',
    padding: '12px',
    backgroundColor: '#f8f9fa',
    borderRadius: '6px',
    border: '1px solid transparent',
    transition: 'all 0.2s'
  },
  stepItemActive: {
    backgroundColor: '#e8f0fe',
    borderColor: COLORS.primary
  },
  stepItemCompleted: {
    backgroundColor: '#e6f4ea'
  },
  stepItemFailed: {
    backgroundColor: '#fce8e6'
  },
  stepIcon: {
    width: '24px',
    height: '24px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '16px',
    marginRight: '12px',
    color: '#999'
  },
  stepIconCompleted: {
    color: COLORS.success,
    fontWeight: 'bold'
  },
  stepIconRunning: {
    color: COLORS.primary
  },
  stepIconFailed: {
    color: COLORS.error,
    fontWeight: 'bold'
  },
  stepContent: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column'
  },
  stepLabel: {
    fontWeight: '500',
    color: '#333',
    marginBottom: '4px'
  },
  stepDescription: {
    fontSize: '13px',
    color: '#666'
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
    border: '1px solid #e0e0e0',
    borderRadius: '8px',
    padding: '20px'
  },
  resultsContent: {
    margin: 0,
    padding: '16px',
    backgroundColor: '#f8f9fa',
    borderRadius: '6px',
    fontSize: '13px',
    overflow: 'auto',
    maxHeight: '300px',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word'
  }
};

export default PipelineInterface;
