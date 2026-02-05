import React from 'react';
import { Amplify } from 'aws-amplify';
import { Authenticator } from '@aws-amplify/ui-react';
import '@aws-amplify/ui-react/styles.css';
import awsconfig from './aws-exports';
import PipelineInterface from './components/PipelineInterface';

Amplify.configure(awsconfig);

function App() {
  return (
    <div style={styles.appContainer}>
      <Authenticator>
        {({ signOut, user }) => (
          <div style={styles.mainContainer}>
            <header style={styles.header}>
              <h1 style={styles.title}>Hacienda SFTP Portal</h1>
              <div style={styles.userInfo}>
                <span style={styles.userName}>
                  {user?.signInDetails?.loginId || 'User'}
                </span>
                <button onClick={signOut} style={styles.signOutButton}>
                  Sign Out
                </button>
              </div>
            </header>

            <main style={styles.main}>
              <div style={styles.card}>
                <PipelineInterface />
              </div>
            </main>

            <footer style={styles.footer}>
              <p>Hacienda ERP Integration System</p>
            </footer>
          </div>
        )}
      </Authenticator>
    </div>
  );
}

const styles = {
  appContainer: {
    minHeight: '100vh',
    backgroundColor: '#f5f5f5'
  },
  mainContainer: {
    minHeight: '100vh',
    display: 'flex',
    flexDirection: 'column'
  },
  header: {
    backgroundColor: '#232f3e',
    color: 'white',
    padding: '16px 24px',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center'
  },
  title: {
    margin: 0,
    fontSize: '24px'
  },
  userInfo: {
    display: 'flex',
    alignItems: 'center',
    gap: '16px'
  },
  userName: {
    fontSize: '14px'
  },
  signOutButton: {
    padding: '8px 16px',
    backgroundColor: 'transparent',
    color: 'white',
    border: '1px solid white',
    borderRadius: '4px',
    cursor: 'pointer',
    fontSize: '14px'
  },
  main: {
    flex: 1,
    padding: '40px 24px',
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'flex-start'
  },
  card: {
    backgroundColor: 'white',
    borderRadius: '12px',
    padding: '32px',
    boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
    maxWidth: '900px',
    width: '100%'
  },
  footer: {
    backgroundColor: '#232f3e',
    color: '#999',
    textAlign: 'center',
    padding: '16px',
    fontSize: '12px'
  }
};

export default App;
