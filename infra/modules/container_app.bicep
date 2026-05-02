param name string
param location string
param logAnalyticsWorkspaceId string
@secure()
param logAnalyticsKey string
param acrLoginServer string
param acrUsername string
@secure()
param acrPassword string
param envVars object
param imageName string = 'rag-chatbot-backend'
param imageTag string = 'latest'

resource env 'Microsoft.App/managedEnvironments@2023-11-02-preview' = {
  name: 'cae-${name}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspaceId
        sharedKey: logAnalyticsKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2023-11-02-preview' = {
  name: name
  location: location
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        { name: 'acr-password', value: acrPassword }
      ]
    }
    template: {
      scale: { minReplicas: 0, maxReplicas: 3 }
      containers: [
        {
          name: 'backend'
          image: '${acrLoginServer}/${imageName}:${imageTag}'
          resources: { cpu: '0.5', memory: '1Gi' }
          env: [for key in objectKeys(envVars): {
            name: key
            value: envVars[key]
          }]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
          ]
        }
      ]
    }
  }
}

output url string = 'https://${app.properties.configuration.ingress.fqdn}'
