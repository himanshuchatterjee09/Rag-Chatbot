param serverName string
param databaseName string
param location string
param adminLogin string
@secure()
param adminPassword string

resource server 'Microsoft.Sql/servers@2023-05-01-preview' = {
  name: serverName
  location: location
  properties: {
    administratorLogin: adminLogin
    administratorLoginPassword: adminPassword
    minimalTlsVersion: '1.2'
  }
}

// Allow Azure services
resource firewall 'Microsoft.Sql/servers/firewallRules@2023-05-01-preview' = {
  parent: server
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource database 'Microsoft.Sql/servers/databases@2023-05-01-preview' = {
  parent: server
  name: databaseName
  location: location
  sku: {
    name: 'GP_S_Gen5_1'  // Serverless — scales to zero, cost-efficient for dev/prod
    tier: 'GeneralPurpose'
    family: 'Gen5'
    capacity: 1
  }
  properties: {
    autoPauseDelay: 60          // pause after 60 min idle
    minCapacity: '0.5'
    requestedBackupStorageRedundancy: 'Local'
  }
}

output serverFqdn string = server.properties.fullyQualifiedDomainName
output databaseName string = database.name
