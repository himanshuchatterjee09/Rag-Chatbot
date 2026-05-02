param name string
param location string

resource workspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: name
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

output workspaceId string = workspace.properties.customerId
output primaryKey string = workspace.listKeys().primarySharedKey
