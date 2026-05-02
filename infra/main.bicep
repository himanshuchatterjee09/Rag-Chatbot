@description('Base name for all resources (3-10 lowercase alphanumeric)')
param baseName string = 'ragchatbot'

@description('Azure region')
param location string = resourceGroup().location

@description('SQL admin username')
param sqlAdminLogin string = 'sqladmin'

@secure()
@description('SQL admin password (min 12 chars, complexity required)')
param sqlAdminPassword string

@description('Azure OpenAI GPT-4o deployment name')
param chatDeployment string = 'gpt-4o'

@description('Azure OpenAI embedding deployment name')
param embeddingDeployment string = 'text-embedding-3-small'

var unique = uniqueString(resourceGroup().id, baseName)
var shortId = take(unique, 6)

// ── Log Analytics ─────────────────────────────────────────────────────────────
module logAnalytics 'modules/log_analytics.bicep' = {
  name: 'logAnalytics'
  params: {
    name: 'log-${baseName}-${shortId}'
    location: location
  }
}

// ── Azure SQL ─────────────────────────────────────────────────────────────────
module sql 'modules/sql.bicep' = {
  name: 'sql'
  params: {
    serverName: 'sql-${baseName}-${shortId}'
    databaseName: 'ai-initiatives-db'
    location: location
    adminLogin: sqlAdminLogin
    adminPassword: sqlAdminPassword
  }
}

// ── Azure AI Search ───────────────────────────────────────────────────────────
module search 'modules/search.bicep' = {
  name: 'search'
  params: {
    name: 'srch-${baseName}-${shortId}'
    location: location
  }
}

// ── Azure OpenAI ──────────────────────────────────────────────────────────────
module openai 'modules/openai.bicep' = {
  name: 'openai'
  params: {
    name: 'oai-${baseName}-${shortId}'
    location: location
    chatDeployment: chatDeployment
    embeddingDeployment: embeddingDeployment
  }
}

// ── Container Registry ────────────────────────────────────────────────────────
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'acr${baseName}${shortId}'
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: true }
}

// ── Container App ─────────────────────────────────────────────────────────────
module containerApp 'modules/container_app.bicep' = {
  name: 'containerApp'
  params: {
    name: 'ca-${baseName}'
    location: location
    logAnalyticsWorkspaceId: logAnalytics.outputs.workspaceId
    logAnalyticsKey: logAnalytics.outputs.primaryKey
    acrLoginServer: acr.properties.loginServer
    acrUsername: acr.listCredentials().username
    acrPassword: acr.listCredentials().passwords[0].value
    envVars: {
      AZURE_OPENAI_ENDPOINT: openai.outputs.endpoint
      AZURE_OPENAI_API_KEY: openai.outputs.apiKey
      AZURE_OPENAI_CHAT_DEPLOYMENT: chatDeployment
      AZURE_OPENAI_EMBEDDING_DEPLOYMENT: embeddingDeployment
      AZURE_SEARCH_ENDPOINT: search.outputs.endpoint
      AZURE_SEARCH_API_KEY: search.outputs.adminKey
      AZURE_SQL_SERVER: sql.outputs.serverFqdn
      AZURE_SQL_DATABASE: 'ai-initiatives-db'
      AZURE_SQL_USERNAME: sqlAdminLogin
      AZURE_SQL_PASSWORD: sqlAdminPassword
    }
  }
}

// ── Function App (weekly sync) ────────────────────────────────────────────────
module funcApp 'modules/function_app.bicep' = {
  name: 'functionApp'
  params: {
    name: 'func-${baseName}-${shortId}'
    location: location
    logAnalyticsWorkspaceId: logAnalytics.outputs.workspaceId
    envVars: {
      AZURE_OPENAI_ENDPOINT: openai.outputs.endpoint
      AZURE_OPENAI_API_KEY: openai.outputs.apiKey
      AZURE_OPENAI_EMBEDDING_DEPLOYMENT: embeddingDeployment
      AZURE_SEARCH_ENDPOINT: search.outputs.endpoint
      AZURE_SEARCH_API_KEY: search.outputs.adminKey
      AZURE_SQL_SERVER: sql.outputs.serverFqdn
      AZURE_SQL_DATABASE: 'ai-initiatives-db'
      AZURE_SQL_USERNAME: sqlAdminLogin
      AZURE_SQL_PASSWORD: sqlAdminPassword
    }
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output chatbotUrl string = containerApp.outputs.url
output sqlServerFqdn string = sql.outputs.serverFqdn
output searchEndpoint string = search.outputs.endpoint
output openaiEndpoint string = openai.outputs.endpoint
output acrLoginServer string = acr.properties.loginServer
