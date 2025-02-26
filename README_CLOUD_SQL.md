# Cloud Run Cloud SQL Connection

This document explains how to use Cloud Run's built-in Cloud SQL connection feature with our application.

## Background

Previously, our application used the Cloud SQL Python Connector library to connect to Cloud SQL instances. While this works well, Cloud Run provides a built-in feature to connect to Cloud SQL that simplifies the connection process and reduces dependencies.

## Benefits of Using Cloud Run's Built-in Cloud SQL Connection

1. **Simplified Connection Logic**: No need to manage the Cloud SQL connector library
2. **Reduced Dependencies**: Removes the need for the `google-cloud-sql-connector` package
3. **Managed Authentication**: Cloud Run handles authentication to Cloud SQL
4. **Automatic Connection Management**: Cloud Run manages the connection lifecycle
5. **Improved Security**: Uses Unix socket connections which are more secure

## How to Enable

To use Cloud Run's built-in Cloud SQL connection:

1. **Enable the feature in your environment**:
   ```
   CLOUD_RUN_CLOUDSQL_ENABLED=true
   ```

2. **Configure Cloud Run to connect to your Cloud SQL instance**:
   
   When deploying to Cloud Run, add the `--add-cloudsql-instances` flag:
   ```
   gcloud run deploy SERVICE_NAME \
     --image IMAGE_URL \
     --add-cloudsql-instances PROJECT_ID:REGION:INSTANCE_NAME \
     --set-env-vars CLOUD_RUN_CLOUDSQL_ENABLED=true
   ```

   Or in the Google Cloud Console:
   - Go to Cloud Run
   - Select your service
   - Click "Edit & Deploy New Revision"
   - Under "Connections", add your Cloud SQL instance
   - Add the environment variable `CLOUD_RUN_CLOUDSQL_ENABLED=true`

3. **Keep your existing environment variables**:
   - `DATABASE_URL`: Your database connection string
   - `INSTANCE_CONNECTION_NAME`: Your Cloud SQL instance connection name (PROJECT_ID:REGION:INSTANCE_NAME)

## How It Works

When `CLOUD_RUN_CLOUDSQL_ENABLED` is set to `true`, the application will:

1. Use the Unix socket provided by Cloud Run at `/cloudsql/INSTANCE_CONNECTION_NAME`
2. Connect to PostgreSQL using this socket instead of the Cloud SQL connector
3. Skip importing the Cloud SQL connector library, reducing dependencies

## Development Environment

For local development, the application will continue to use direct connections to your database. The Cloud Run Cloud SQL connection is only used in production when deployed to Cloud Run with a Cloud SQL instance connected.

## Troubleshooting

If you encounter issues with the Cloud Run Cloud SQL connection:

1. **Check Cloud Run Configuration**: Ensure your Cloud SQL instance is properly connected to your Cloud Run service
2. **Verify Environment Variables**: Make sure `CLOUD_RUN_CLOUDSQL_ENABLED` is set to `true`
3. **Check Logs**: Look for connection-related log messages
4. **Fallback Option**: Set `CLOUD_RUN_CLOUDSQL_ENABLED` to `false` to revert to using the Cloud SQL connector library

## Additional Resources

- [Cloud Run Cloud SQL Connection Documentation](https://cloud.google.com/sql/docs/mysql/connect-run)
- [Cloud SQL Connection Options](https://cloud.google.com/sql/docs/mysql/connect-overview) 