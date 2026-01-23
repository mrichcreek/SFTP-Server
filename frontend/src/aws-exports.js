// AWS Amplify Configuration
// Amplify App: SFTP-Server (dsd8pjdmo3d2x)
// Domain: https://main.dsd8pjdmo3d2x.amplifyapp.com/

const awsconfig = {
  // Cognito configuration
  Auth: {
    Cognito: {
      userPoolId: 'us-east-1_B9L2aprTj',
      userPoolClientId: '39dbtnt6f5s0li79erji1lqbps',
      region: 'us-east-1',
      signUpVerificationMethod: 'code',
      loginWith: {
        email: true
      },
      passwordFormat: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireNumbers: true
      }
    }
  },
  // API Gateway configuration - Update after deploying backend infrastructure
  API: {
    REST: {
      'haciendaApi': {
        endpoint: 'https://placeholder.execute-api.us-east-1.amazonaws.com/prod',
        region: 'us-east-1'
      }
    }
  }
};

export default awsconfig;
