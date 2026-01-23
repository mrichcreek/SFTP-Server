import React from 'react';

function ProgressBar({ progress, status, message }) {
  const getStatusColor = () => {
    switch (status) {
      case 'completed':
        return '#4caf50';
      case 'failed':
        return '#f44336';
      case 'retrying':
        return '#ff9800';
      default:
        return '#2196f3';
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.progressContainer}>
        <div
          style={{
            ...styles.progressBar,
            width: `${progress}%`,
            backgroundColor: getStatusColor()
          }}
        />
      </div>
      <div style={styles.info}>
        <span style={styles.percentage}>{Math.round(progress)}%</span>
        <span style={styles.status}>{status}</span>
      </div>
      {message && <p style={styles.message}>{message}</p>}
    </div>
  );
}

const styles = {
  container: {
    width: '100%',
    marginTop: '20px'
  },
  progressContainer: {
    width: '100%',
    height: '24px',
    backgroundColor: '#e0e0e0',
    borderRadius: '12px',
    overflow: 'hidden'
  },
  progressBar: {
    height: '100%',
    transition: 'width 0.3s ease-in-out',
    borderRadius: '12px'
  },
  info: {
    display: 'flex',
    justifyContent: 'space-between',
    marginTop: '8px'
  },
  percentage: {
    fontWeight: 'bold',
    color: '#333'
  },
  status: {
    textTransform: 'capitalize',
    color: '#666'
  },
  message: {
    marginTop: '8px',
    color: '#666',
    fontSize: '14px'
  }
};

export default ProgressBar;
