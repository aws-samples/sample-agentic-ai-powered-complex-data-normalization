# Deployment and operations guide

This guide covers operating the deployed pipeline: configuration, monitoring, scaling, and troubleshooting. For deployment steps, see the [README](../README.md#deploy-to-aws).

## Architecture

S3 upload (`inbox/` prefix) → EventBridge → splitter Lambda → SQS (25 records per message) → processor Lambda → DynamoDB

![Architecture diagram: a producer uploads a CSV to an S3 input bucket; EventBridge triggers a splitter Lambda that batches records into SQS; a processor Lambda runs a Strands agent that screens input with an Amazon Bedrock Guardrail, calls Claude Sonnet 5 on Amazon Bedrock, and searches through an Amazon Bedrock AgentCore Gateway with the Web Search tool; results go to DynamoDB and an optional S3 output bucket, traces go to CloudWatch, and data is encrypted with AWS KMS](architecture/architecture.svg)

To update the diagram, edit `architecture/architecture.drawio` in [draw.io](https://www.drawio.com/) and export it to `architecture/architecture.svg`.

## Components

| Component | Configuration |
|---|---|
| Splitter Lambda | Reads the CSV, creates batches of `BATCH_SIZE` (default 25), sends them to SQS. 5-minute timeout, 1 GB memory, reserved concurrency 5. Rejects uploads above `MAX_ROWS` (default 100,000). |
| SQS queue | 16-minute visibility timeout, 24-hour retention. Messages move to the dead-letter queue after 3 failed receives. |
| Processor Lambda | Classifies each record in a batch with the Strands agent. 15-minute timeout, 2 GB memory, reserved concurrency 100, one SQS message per invocation, ADOT layer for tracing. |
| DynamoDB | On-demand billing, point-in-time recovery enabled, encrypted with the stack's KMS key. Partition key `charge_id`, sort key `processing_timestamp`, secondary index `code-index` on `code`. |

## Configuration

Processor environment variables:

| Variable | Default | Description |
|---|---|---|
| `TAXONOMY` | `law_enforcement` | Classification profile. Set with the `Taxonomy` stack parameter. |
| `REVIEW_THRESHOLD` | `0.60` | Confidence below this sets `needs_review`. Set with the `ReviewThreshold` stack parameter. |
| `MODEL_CONFIG` | `{"model_id":"us.anthropic.claude-sonnet-5","max_tokens":8000}` | Passed to the Strands `BedrockModel`. |
| `SEARCH_CONFIG` | `{"max_results":3,"content_length":500}` | Results per search, and characters of each result passed to the model. |
| `WEB_SEARCH_GATEWAY_URL` | (stack output) | The AgentCore Gateway MCP endpoint for web search. |

Claude Sonnet 5 does not accept sampling parameters (`temperature`, `top_p`, `top_k`), so do not add them to `MODEL_CONFIG`. `max_tokens` must leave room for the model's reasoning plus the final JSON output.

To change configuration, edit `template.yaml` or the stack parameters and redeploy:

```bash
sam build -t template.yaml
sam deploy --stack-name classification-agents-dev --capabilities CAPABILITY_IAM \
  --region us-east-1 --resolve-s3 \
  --parameter-overrides ReviewThreshold=0.70
```

Avoid `aws lambda update-function-configuration --environment`: it replaces the function's entire set of environment variables, which removes the table name, guardrail ID, and other values the processor needs.

## Observability

The processor uses the Strands Agents SDK OpenTelemetry support with the AWS Distro for OpenTelemetry (ADOT) Lambda layer. Traces include agent invocations, tool calls, model latency, and token usage, and appear in the CloudWatch GenAI observability views. The processor logs record IDs only, never input text, and CloudWatch Logs data protection policies mask PII in both functions' log groups.

For per-record token usage, read the `chat` spans (one per model call). The processor reuses one agent across records in a warm container, so the token counts on the `invoke_agent` span accumulate across records and overstate a single record's usage.

## Monitoring

Key metrics:

- Processor errors and duration (watch duration against the 900-second timeout)
- Processor concurrent executions (up to the reserved concurrency)
- Dead-letter queue depth (should be 0)
- Share of results with `needs_review = true`

The stack creates two CloudWatch alarms: processor errors above 100, and dead-letter queue depth above 10.

```bash
# Processor logs
aws logs tail /aws/lambda/classification-agents-dev-processor --follow

# Splitter logs
aws logs tail /aws/lambda/classification-agents-dev-splitter --follow

# Errors only
aws logs filter-log-events \
  --log-group-name /aws/lambda/classification-agents-dev-processor \
  --filter-pattern "ERROR"
```

## Scaling

Throughput is bounded by the processor's reserved concurrency (default 100) and your Amazon Bedrock quotas. Before raising `ReservedConcurrentExecutions`:

- Check your Bedrock requests-per-minute and tokens-per-minute quotas for the model in the [Service Quotas console](https://console.aws.amazon.com/servicequotas/), and request increases if needed. Reasoning tokens count as output tokens, so size token quotas from measured usage.
- Treat concurrency as a cost control. Each concurrent processor makes model and search calls continuously.

See [Estimate throughput and cost](../README.md#estimate-throughput-and-cost) for the formulas.

## Troubleshooting

### Processor timeouts or duplicate results

A batch runs sequentially inside one invocation, so `BATCH_SIZE x seconds_per_record` must stay under the 900-second timeout. When a batch times out, SQS delivers the message again after the visibility timeout, and records that already finished are classified and written a second time. Lower `BATCH_SIZE` (for example, to 10) if processor duration approaches the timeout.

### Throttling

Lambda throttling or Bedrock `ThrottlingException` errors mean concurrency exceeds your quotas. Lower reserved concurrency or request a Bedrock quota increase.

### Messages in the dead-letter queue

1. Check the processor logs for the error.
2. Fix the underlying issue.
3. Move the messages back to the main queue with an SQS [dead-letter queue redrive](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-configure-dead-letter-queue-redrive.html).

### Many results need review

- Check input quality: typos, abbreviations, and missing context lower confidence.
- Provide context fields (for law enforcement: state, county, charge code).
- Check whether the guardrail is blocking inputs: blocked records have `error = guardrail_intervened`.
- Refine the profile's research steps and guidance in `lambda_functions/taxonomies/`.
- Adjust `ReviewThreshold` only after measuring accuracy against a labeled sample.

### Unexpected cost

- Set `BudgetAlertEmail` so the budget alarm notifies you.
- Check searches and model turns per record in the traces. Each search adds a model turn.
- Lower `max_results` in `SEARCH_CONFIG` to reduce input tokens per turn.

## Security notes

- Web search uses Web Search on Amazon Bedrock AgentCore through an AgentCore Gateway with IAM (SigV4) inbound authorization. No search API key exists. The gateway's service role can call only the Web Search tool, and the processor's role can invoke only this stack's gateway.
- The Lambda functions do not run in a VPC; they reach AWS services over public AWS endpoints using TLS. S3 bucket policies deny non-TLS requests. If your requirements call for private connectivity, add VPC configuration and VPC endpoints.
- Each function has its own IAM role. The Bedrock policy allows invoking Anthropic Claude models; scope it to the specific model or inference profile you use.
- Strands records full prompts and responses in trace events in the account-level `aws/spans` log group. See the README's [Security considerations](../README.md#security-considerations).

## Backup and recovery

- DynamoDB point-in-time recovery is enabled. Restore to a new table if you need to recover data.
- The S3 buckets are versioned. The input bucket expires objects after 90 days; the output bucket transitions objects to a cheaper storage class after 30 days.

Deleting the stack deletes the DynamoDB table and its data, and schedules the KMS key for deletion. Export results you want to keep first. See [Clean up](../README.md#clean-up) for teardown steps.
