@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Prefix for all resource names')
param namePrefix string = 'eaa'

@description('Container image to deploy (e.g. from Azure Container Registry)')
param containerImage string = 'eaa-app:latest'

@description('API key for the app (admin role). Use Key Vault in production.')
@secure()
param appApiKey string = ''

@description('Azure OpenAI endpoint and deployment names (deploy separately)')
param azureOpenAIEndpoint string = ''
param azureOpenAIDeployment string = 'gpt-4o-mini'
param azureEmbeddingDeployment string = 'text-embedding-3-small'

@description('Enable managed identity for Azure OpenAI / AI Search')
param useManagedIdentity bool = true

var appName = '${namePrefix}-app'
var envName = '${namePrefix}-env'
var searchName = '${namePrefix}-search'
var kvName = '${namePrefix}-kv'
var redisName = '${namePrefix}-redis'
var lawName = '${namePrefix}-logs'

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: lawName
  location: location
  properties: { retentionInDays: 30 }
}

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'azure-monitor'
      logAnalyticsConfiguration: { customerId: law.properties.customerId }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
      }
      registries: []
    }
    template: {
      containers: [
        {
          name: 'app'
          image: containerImage
          resources: { cpu: json('1.0'), memory: '2Gi' }
          probes: [
            {
              type: 'liveness'
              httpGet: { path: '/api/v1/health', port: 8000 }
              initialDelaySeconds: 20
              periodSeconds: 30
            }
          ]
          env: [
            { name: 'APP_HOST', value: '0.0.0.0' }
            { name: 'APP_PORT', value: '8000' }
            { name: 'APP_DEBUG', value: 'false' }
            { name: 'AUTH_ENABLED', value: appApiKey == '' ? 'false' : 'true' }
            { name: 'API_KEYS', value: appApiKey == '' ? '' : '${appApiKey}:admin' }
            { name: 'AZURE_USE_MANAGED_IDENTITY', value: string(useManagedIdentity) }
            { name: 'AZURE_OPENAI_ENDPOINT', value: azureOpenAIEndpoint }
            { name: 'AZURE_OPENAI_DEPLOYMENT_NAME', value: azureOpenAIDeployment }
            { name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', value: azureEmbeddingDeployment }
            { name: 'AZURE_AI_SEARCH_ENDPOINT', value: 'https://${searchName}.search.windows.net' }
            { name: 'AZURE_AI_SEARCH_INDEX_NAME', value: 'enterprise-kb' }
            { name: 'QDRANT_HOST', value: 'localhost' }
            { name: 'REDIS_HOST', value: redisName }
            { name: 'REDIS_PORT', value: '6380' }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
}

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchName
  location: location
  sku: { name: 'basic' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
  }
}

resource redis 'Microsoft.Cache/redis@2023-08-01' = {
  name: redisName
  location: location
  properties: {
    sku: { name: 'Basic', family: 'C', capacity: 0 }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
  }
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    accessPolicies: []
  }
}

// Grant the app's managed identity read access to Key Vault secrets.
resource kvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, app.id, 'KeyVaultSecretsUser')
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6'
    )
    principalId: app.identity.principalId
  }
}

output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'
output appPrincipalId string = app.identity.principalId
output keyVaultUri string = kv.properties.vaultUri
output searchEndpoint string = 'https://${searchName}.search.windows.net'
