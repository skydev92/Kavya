# Kavya Langfuse Integration

> **⚠️ Important**: This is Kavya's custom Langfuse integration documentation.  
> For general Langfuse documentation, see [langfuse.com/docs](https://langfuse.com/docs)

## Overview
This document covers Kavya's specific implementation of Langfuse integration for comprehensive LLM observability, cost tracking, and performance monitoring.

## Core Features
This integration focuses on three key features:

1. **User Feedback Collection** - Capturing thumbs up/down reactions and comments from users
2. **Error Notifications** - Monitoring and alerting on errors during AI request handling
3. **Weekend Email Reports** - Generating and distributing weekly reports with key metrics

## Implementation Details

### 1. User Feedback Collection (16 hours)

Langfuse directly supports this through its **Scores** feature, which is part of the broader Evaluations capability. Scores allow us to attach numerical values (e.g., 1 for thumbs up, 0 for thumbs down) and textual comments to specific traces.

#### Implementation Approach:
- **Instrument Frontend/Backend**: Use the appropriate Langfuse SDK (JavaScript Web SDK for frontend feedback capture and Python/other backend SDKs if feedback is submitted via API) to send score data.
- **Capture Feedback**: When a user clicks thumbs up/down or submits a comment, trigger a function that calls the Langfuse `score()` method.
- **Associate with Trace**: Ensure the score call includes the `traceId` of the specific AI interaction the user is providing feedback on. This links the feedback directly to the relevant request/response flow in Langfuse.
- **Analyze Feedback**: Use the Langfuse UI (Trace view, Dashboards) to view feedback scores and comments associated with traces, filter traces by scores, and analyze user satisfaction trends.

#### Solution Notes:
This implementation will involve adding UI elements (buttons, comment fields) and instrumenting the frontend to capture and send the score data, ensuring the `traceId` is available in the frontend context.

### 2. Error Notification (12 hours)

Langfuse captures errors as part of its tracing mechanism. When instrumenting code, we can explicitly mark traces or spans (sub-steps within a trace) with an `ERROR` level and provide a `statusMessage` detailing the error.

#### Implementation Approach:
- **Log Errors to Langfuse**: Within the exception handling blocks, update the relevant Langfuse observation (trace or span) to set the `level` to `"ERROR"` and include the error details in the `statusMessage`.
- **Monitor Errors in Langfuse UI**: Use the Langfuse UI to filter traces by the `ERROR` level. The dashboard can be configured to show error rates over time.
- **External Alerting (Workaround)**: Since Langfuse itself does not offer built-in direct notification mechanisms (like email or Slack alerts) triggered by errors, we'll develop a custom service to:
  - Periodically query the Langfuse API for traces with ERROR level
  - Generate notifications for new errors
  - Send alerts through appropriate channels (email, Slack, etc.)

#### Solution Notes:
The error notification system will combine Langfuse's native error tracking with a custom service that interfaces with the Langfuse API to provide real-time alerts to appropriate team members.

### 3. Weekend Email Reports (12 hours)

While Langfuse provides Custom Dashboards for visualizing key metrics, it does not currently offer built-in functionality for automatically generating and emailing dashboard reports on a schedule.

#### Implementation Approach:
- **Define Key Metrics**: We will identify the specific metrics to include in the weekend report:
  - Total requests processed
  - Average response latency
  - Total cost incurred
  - Error count and rate
  - Average user feedback score
  - Most common error types

- **Configure Langfuse Dashboards**: Create widgets on a Langfuse Custom Dashboard to visualize these key metrics for manual reference.

- **Automated Reporting via API**:
  - Develop a service that uses the Langfuse API to query data for key metrics over the desired time period (past week)
  - Format the data into a readable report with key insights highlighted
  - Generate visualizations for important metrics
  - Distribute the report via email using an appropriate service (SendGrid, AWS SES, etc.)
  - Schedule the service to run automatically every weekend (e.g., Saturday morning)

#### Solution Notes:
The weekend reporting system will run as a separate microservice with scheduled execution. It will connect to the Langfuse API using appropriate authentication and generate standardized reports for stakeholders.

## Technical Requirements

### Langfuse Configuration
- Langfuse account setup with appropriate access permissions
- API keys for both public and secret access
- Proper configuration of trace retention periods and data storage settings

### Development Requirements
- Frontend SDK integration for user-facing components
- Backend SDK integration for server-side observability
- Custom notification service development
- Report generation service development
- Scheduling infrastructure (e.g., cron jobs or serverless functions)

### Infrastructure Needs
- Secure storage for Langfuse API credentials
- Execution environment for periodic jobs (notification checks and report generation)
- Email service integration for delivering reports and alerts

## Timeline

The estimated implementation timeline is as follows:

| Feature | Time Estimate | Priority | Dependencies |
|---------|---------------|----------|--------------|
| User Feedback Collection | 16 hours | High | Langfuse SDK integration |
| Error Notification | 12 hours | Medium | Langfuse SDK integration, Alert service |
| Weekend Email Reports | 12 hours | Medium | Langfuse API access, Report service, Email integration |
| **Total** | **40 hours** | | |

## Success Metrics

We will measure the success of this integration by tracking:

1. **User Feedback Coverage**: Percentage of AI interactions that receive user feedback
2. **Alert Response Time**: Time between error occurrence and team notification
3. **Report Utilization**: Whether stakeholders find the weekend reports actionable
4. **System Reliability**: Ensuring the integration itself does not introduce errors

## Future Enhancements

Potential future improvements to consider:

- Implement more granular feedback options beyond binary thumbs up/down
- Create a real-time error dashboard with advanced filtering
- Expand weekend reports to include cost analysis and optimization recommendations
- Set up automated evaluation of AI responses using Langfuse's evaluation framework

## Observation Hierarchy Implementation

### Trace and Observation Structure

Langfuse **Observations** are used to group related LLM calls that belong to the same agentic feature. We've implemented a proper hierarchy where:

- **Traces** represent entire user requests
- **Parent Observations** represent agentic workflows (longwriter, web_search, routing)  
- **Child Generations** represent individual LLM calls within each workflow

#### Observation Hierarchy

1. **Longwriter Workflow**
   - Parent Observation: `longwriter` (trace_name: "longwriter_workflow")
   - Child Generations:
     - `content_strategy_generator` (step: generate_content_strategy)
     - `html_strategy_generator` (step: generate_html_strategy) 
     - `content_outline_generator` (step: generate_content_outline)
     - `content_writer_streaming` (step: write_content)

2. **Web Search Workflow**
   - Parent Observation: `web_search` (trace_name: "web_search_workflow")
   - Child Generations:
     - `web_search_need_analyzer` (step: analyze_search_need)
     - `web_search_query_generator` (step: generate_search_queries)
     - `perplexity_web_search` (step: execute_search)

3. **Routing Workflow**
   - Parent Observation: `router` (trace_name: "routing_workflow")
   - Child Generations:
     - `controller_router` (step: route_to_controller)

### Benefits

In the Langfuse UI, you can now:
- View the **Observations** tab to see grouped agentic workflows
- Analyze performance and costs by complete workflows rather than individual calls
- Track how often each agentic feature is used
- Debug multi-step workflows more easily

## Tag Implementation

### Overview
Tags have been implemented across all completion calls to enable better filtering and analysis in Langfuse. Tags are hierarchical and follow a consistent naming convention.

### Tag Structure

#### Base Tags (All Calls)
- `controller:{ControllerClassName}` - The controller handling the request
- `model:{model_name}` - The actual model used for completion
- `env:{environment}` - The deployment environment (development/staging/production)
- `retry:{attempt_number}` - Added when retrying failed requests
- `fallback` - Added when using fallback models

#### Agent-Specific Tags

**Web Search Agent**
- `agent:web_search` - Identifies web search operations
- `step:analyze_need` - Analyzing if search is needed
- `step:generate_queries` - Generating search queries
- `step:single_query` - Generating a single query
- `step:execute` - Executing the search
- `provider:perplexity` - When using Perplexity for search
- `query_count:{n}` - Number of queries generated

**Content Writer Agent**
- `agent:content_writer` - Identifies content writing operations
- `step:strategy` - Generating content strategy
- `step:html_strategy` - Generating HTML structure
- `step:outline` - Creating content outline
- `step:write` - Writing the actual content
- `longwriter` - Identifies long-form content generation
- `streaming` - For streaming responses
- `words:{count}` - Target word count

**Router Agent**
- `agent:router` - Identifies routing operations
- `routing` - General routing tag
- `controllers:{n}` - Number of available controllers

### Using Tags in Langfuse

#### Filtering Examples
1. Find all web searches: Filter by tag `agent:web_search`
2. Find all retries: Filter by tag contains `retry:`
3. Find all streaming requests: Filter by tag `streaming`
4. Find all production errors: Filter by tags `env:production` AND level `ERROR`

#### Analytics Use Cases
- **Performance by Agent**: Group by `agent:*` tags
- **Fallback Analysis**: Filter by `fallback` tag
- **Model Usage**: Group by `model:*` tags
- **Retry Patterns**: Analyze `retry:*` tags

## Resources

- [Langfuse Documentation](https://langfuse.com/docs)
- [JavaScript SDK Reference](https://langfuse.com/docs/sdk/javascript)
- [Python SDK Reference](https://langfuse.com/docs/sdk/python)