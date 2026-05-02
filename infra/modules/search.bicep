param name string
param location string

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: name
  location: location
  sku: { name: 'basic' }   // supports semantic search; upgrade to standard for production scale
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    semanticSearch: 'free'
  }
}

output endpoint string = 'https://${search.name}.search.windows.net'
output adminKey string = search.listAdminKeys().primaryKey
