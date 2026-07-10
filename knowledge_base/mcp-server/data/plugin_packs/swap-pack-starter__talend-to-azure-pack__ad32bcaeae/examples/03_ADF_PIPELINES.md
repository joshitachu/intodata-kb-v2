# Example ADF Pipelines Bundle

This example shows structure and tone only. Replace all names with
project-specific evidence during generation; never copy placeholder names
into final output.

## Deployment Instructions

1. Create the Key Vault secrets listed in the Parameter Reference.
2. Save each artifact below under its stated path in the ADF Git repository.
3. Publish linked services, then datasets, then pipelines, then triggers.

## Linked Services

**File: linkedService/LS_KeyVault.json**

```json
{
  "name": "LS_KeyVault",
  "properties": {
    "type": "AzureKeyVault",
    "typeProperties": {
      "baseUrl": "https://@{pipeline().globalParameters.keyVaultName}.vault.azure.net/"
    }
  }
}
```

**File: linkedService/LS_SqlServer_ExampleConn.json**

```json
{
  "name": "LS_SqlServer_ExampleConn",
  "properties": {
    "type": "SqlServer",
    "connectVia": { "referenceName": "SelfHostedIR", "type": "IntegrationRuntimeReference" },
    "typeProperties": {
      "connectionString": {
        "type": "AzureKeyVaultSecret",
        "store": { "referenceName": "LS_KeyVault", "type": "LinkedServiceReference" },
        "secretName": "kv-exampleconn-connectionstring"
      }
    }
  }
}
```

## Datasets

**File: dataset/DS_SqlServer_ExampleTable.json**

```json
{
  "name": "DS_SqlServer_ExampleTable",
  "properties": {
    "linkedServiceName": { "referenceName": "LS_SqlServer_ExampleConn", "type": "LinkedServiceReference" },
    "type": "SqlServerTable",
    "typeProperties": { "schema": "dbo", "table": "ExampleTable" },
    "schema": [
      { "name": "Id", "type": "int" },
      { "name": "Name", "type": "nvarchar" }
    ]
  }
}
```

## Pipelines

**File: pipeline/PL_ExampleJob.json**

```json
{
  "name": "PL_ExampleJob",
  "properties": {
    "description": "Replaces Talend job ExampleJob v1.0",
    "activities": [
      {
        "name": "Copy_ExampleTable",
        "type": "Copy",
        "inputs": [{ "referenceName": "DS_SqlServer_ExampleTable", "type": "DatasetReference" }],
        "outputs": [{ "referenceName": "DS_Blob_ExampleLanding", "type": "DatasetReference" }],
        "typeProperties": {
          "source": { "type": "SqlServerSource" },
          "sink": { "type": "DelimitedTextSink" }
        }
      }
    ]
  }
}
```

## Triggers

**File: trigger/TR_ExampleJob.json**

```json
{
  "name": "TR_ExampleJob",
  "properties": {
    "runtimeState": "Stopped",
    "type": "ScheduleTrigger",
    "typeProperties": {
      "recurrence": { "frequency": "Day", "interval": 1, "startTime": "2026-01-01T02:00:00Z", "timeZone": "UTC" }
    },
    "pipelines": [{ "pipelineReference": { "referenceName": "PL_ExampleJob", "type": "PipelineReference" } }]
  }
}
```

## Parameter Reference

| Parameter | Talend origin | Example value | Key Vault |
|---|---|---|---|
| keyVaultName | n/a (new) | kv-example-dev | no |
| kv-exampleconn-connectionstring | context.DB_HOST, context.DB_PASSWORD | (secret) | yes |

## Evidence Notes

- Example only: real output must list placeholder activities and evidence
  gaps per pipeline here.
