param name string
param location string
param chatDeployment string
param embeddingDeployment string

resource account 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = {
  name: name
  location: location
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
  }
}

resource gpt4o 'Microsoft.CognitiveServices/accounts/deployments@2023-10-01-preview' = {
  parent: account
  name: chatDeployment
  sku: { name: 'Standard', capacity: 30 }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-11-20'
    }
  }
}

resource embeddings 'Microsoft.CognitiveServices/accounts/deployments@2023-10-01-preview' = {
  parent: account
  name: embeddingDeployment
  dependsOn: [gpt4o]
  sku: { name: 'Standard', capacity: 120 }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-small'
      version: '1'
    }
  }
}

output endpoint string = account.properties.endpoint
output apiKey string = account.listKeys().key1
