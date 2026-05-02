param name string
param location string
param logAnalyticsWorkspaceId string
param envVars object

var storageName = replace(replace(toLower(name), '-', ''), '_', '')

resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: take('st${storageName}', 24)
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
}

resource plan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: 'asp-${name}'
  location: location
  sku: { name: 'Y1', tier: 'Dynamic' }
  properties: { reserved: true }  // Linux
}

resource func 'Microsoft.Web/sites@2023-01-01' = {
  name: name
  location: location
  kind: 'functionapp,linux'
  properties: {
    serverFarmId: plan.id
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      appSettings: concat(
        [
          { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value}' }
          { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
          { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
          { name: 'APPINSIGHTS_INSTRUMENTATIONKEY', value: '' }
        ],
        [for key in objectKeys(envVars): {
          name: key
          value: envVars[key]
        }]
      )
    }
    httpsOnly: true
  }
}
