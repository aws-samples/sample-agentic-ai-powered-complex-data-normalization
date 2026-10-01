# Building AI agents for domain-specific classification at scale

This sample shows how to build an agentic classification system on AWS that turns unstructured, domain-specific text into standardized codes. Each classification is explainable: the agent researches authoritative sources, returns a confidence score and reasoning, cites the sources it relied on, and flags uncertain results for human review. It uses the [Strands Agents SDK](https://github.com/strands-agents/sdk-python), [Amazon Bedrock](https://aws.amazon.com/bedrock/), [Web Search on Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-web-search-tool.html), and serverless services.

It accompanies the AWS Public Sector Blog post [Building AI agents for domain-specific classification at scale](https://aws.amazon.com/blogs/publicsector/building-ai-agents-for-domain-specific-classification-at-scale/).

The included worked example is the one from the blog post: classifying free-text criminal charges (with optional state, county, statute code, offense type, and date) into FBI NIBRS offense codes. The domain-specific parts are isolated in a single profile module, so you can adapt the same agent and pipeline to other domains, such as diagnosis coding or regulatory transaction categories. See [Adapt to your domain](#adapt-to-your-domain).

## Highlights

- **Explainable by design.** Every result carries a confidence score, the agent's reasoning, and citations to the sources it used.
- **Citations you can trust.** Source URLs are checked against what the search actually returned, so the audit trail only contains real, retrieved sources.
- **Built-in review routing.** Low-confidence or uncertain results are flagged for human review automatically.
- **No search API keys.** Web search runs through Web Search on Amazon Bedrock AgentCore, deployed with the stack and served within AWS.
- **Guarded input.** Amazon Bedrock Guardrails blocks prompt attacks and masks personal information before the model sees a record.
- **Serverless and scalable.** Amazon S3, Amazon EventBridge, AWS Lambda, Amazon SQS, and Amazon DynamoDB, deployed with one AWS SAM template.
- **Adaptable to your domain.** Swap the code set, input fields, and prompt in one profile module.

> **About this sample:** This sample demonstrates the architecture and patterns from the blog post. Before using it in production, evaluate classification quality on representative data from your domain, review the [responsible use](#responsible-use) and [security](#security-considerations) guidance, and keep a qualified person in the loop for consequential decisions.

## Contents

- [How it works](#how-it-works)
- [What each classification contains](#what-each-classification-contains)
- [Prerequisites](#prerequisites)
- [Deploy to AWS](#deploy-to-aws)
- [Use the deployed pipeline](#use-the-deployed-pipeline)
- [Run locally](#run-locally)
- [Estimate throughput and cost](#estimate-throughput-and-cost)
- [Adapt to your domain](#adapt-to-your-domain)
- [Responsible use](#responsible-use)
- [Security considerations](#security-considerations)
- [Clean up](#clean-up)
- [Project structure](#project-structure)

## How it works

![Architecture diagram: a producer uploads a CSV to an S3 input bucket; EventBridge triggers a splitter Lambda that batches records into SQS; a processor Lambda runs a Strands agent that screens input with an Amazon Bedrock Guardrail, calls Claude Sonnet 5 on Amazon Bedrock, and searches through an Amazon Bedrock AgentCore Gateway with the Web Search tool; results go to DynamoDB and an optional S3 output bucket, traces go to CloudWatch, and data is encrypted with AWS KMS](docs/architecture/architecture.svg)

1. A producer uploads a CSV file to the `inbox/` prefix of an Amazon S3 input bucket.
2. Amazon EventBridge reacts to the object-created event and triggers a splitter AWS Lambda function, which batches records and sends them to Amazon SQS.
3. A processor Lambda function consumes each batch and invokes the Strands agent for every record. The agent calls Amazon Bedrock for model inference. Before the model sees a record, an Amazon Bedrock Guardrail screens the untrusted input text.
4. The agent uses the context provided (jurisdiction, dates, codes) and calls a web search tool to retrieve authoritative sources, such as statutes, regulations, and official guidelines, then produces a classification with reasoning and citations. Search runs through the Web Search tool on an Amazon Bedrock AgentCore Gateway that the stack creates, so there is no search API key to manage and queries are served within AWS.
5. The processor writes each result, including confidence score, verified source URLs, and a review flag, to Amazon DynamoDB. An S3 output bucket is provisioned for exporting results to downstream systems.
6. Amazon CloudWatch captures OpenTelemetry traces and token metrics for agent observability, including reasoning steps, tool invocations, and model latency.

The agent follows the reasoning process described in the blog post: analyze the input, research authoritative sources, synthesize a decision, assess confidence, and document the rationale with citations.

## What each classification contains

| Field | Name | Description |
|---|---|---|
| Code | `code` | Standardized code (a NIBRS offense code in this example). Must be one of the profile's codes, or the result falls back to `90Z`. |
| Description | `description` | Human-readable name, filled in from the code table (not from the model). |
| Confidence | `confidence` | 0.0 to 1.0. |
| Reasoning | `reasoning` | Explanation citing the sources used. |
| Citations | `sources` | `[{title, url}]`. Only URLs the search tool actually returned are kept. |
| Review flag | `needs_review` | `true` when confidence is below `REVIEW_THRESHOLD` (default 0.60), the fallback code was used, or an error occurred. |
| Grounding | `web_grounded` | `true` if the agent ran at least one search for this record. |
| Reference | `statute_reference` | Statute the agent relied on, if found. |

Three of these fields are set in code rather than taken from the model, so the model cannot misreport them: `web_grounded` comes from a counter in the search tool, `sources` is filtered against the URLs the search tool returned, and `needs_review` is computed from the confidence threshold.

> **Note:** This repository uses Claude Sonnet 5. The blog's `statute_code` input field is supported as an alias for `charge_code`.

## Prerequisites

- An AWS account with [model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html) to Anthropic Claude Sonnet 5 in Amazon Bedrock.
- A Region where Web Search on Amazon Bedrock AgentCore is available. The sample is tested in US East (N. Virginia) `us-east-1`. The default model ID uses a US cross-Region inference profile (`us.`); to deploy elsewhere, change `MODEL_CONFIG` to a profile for that geography.
- Python 3.12 or later.
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials for that account.
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html), for deployment only.

## Run locally

Local runs call Amazon Bedrock and the stack's web search gateway from your machine using your AWS credentials, so [deploy the stack](#deploy-to-aws) first. They are useful for development and small batches. Your credentials need `bedrock:InvokeModel` and `bedrock-agentcore:InvokeGateway` on the gateway.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
STACK=classification-agents-dev
out() { aws cloudformation describe-stacks --stack-name $STACK \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
export WEB_SEARCH_GATEWAY_URL=$(out WebSearchGatewayUrl)
# Enable the same Bedrock Guardrail input screening the deployed pipeline uses
export GUARDRAIL_ID=$(out GuardrailId) GUARDRAIL_VERSION=$(out GuardrailVersion)
```

Without `GUARDRAIL_ID`, local runs skip input screening (prompt-attack blocking and PII masking) and print a warning. Your credentials also need `bedrock:ApplyGuardrail` on the guardrail.

The local CLI and web UI use the `law_enforcement` profile.

### Command line

```bash
# Classify one record
python3 strands_agent_classifier.py \
  --charge "DWI 2nd Offense" --state TX --county "Travis County" \
  --code 49.04 --type Misdemeanor

# Run the three built-in samples
python3 strands_agent_classifier.py

# Classify records from a CSV (default: first 10)
python3 strands_agent_classifier.py --csv test/test_with_context.csv --max 10
```

Results print to the console and are written to `strands_results_<timestamp>.json`. Add `--show-reasoning` to also print the agent's reasoning, which can restate the input record.

### Web UI

The Streamlit UI shows the agent's result, reasoning, verified sources, and review flag. It refuses to start without a password, and is intended for local use only: do not expose it on a network.

```bash
read -rs -p "Choose a local UI password: " APP_PASSWORD && export APP_PASSWORD
python3 -m streamlit run streamlit_ui.py
```

Open http://localhost:8501.

### Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests stub Strands and the HTTP layer, so they run offline and make no AWS calls.

### Update dependencies

Direct dependencies are listed in the `.in` files. The `requirements.txt` files are generated locks that pin every package, including transitive ones, so builds are reproducible and dependency scanners evaluate the versions that actually ship. After editing a `.in` file, regenerate the locks with [uv](https://docs.astral.sh/uv/):

```bash
uv pip compile lambda_functions/requirements.in --python-version 3.12 \
  --python-platform x86_64-manylinux_2_28 -o lambda_functions/requirements.txt
uv pip compile requirements.in --python-version 3.12 --universal -o requirements.txt
uv pip compile requirements-dev.in --python-version 3.12 --universal -o requirements-dev.txt
```

Then audit them, for example with `pip-audit -r requirements.txt`.

## Deploy to AWS

```bash
git clone https://github.com/aws-samples/sample-agentic-ai-powered-complex-data-normalization.git
cd sample-agentic-ai-powered-complex-data-normalization
sam build -t template.yaml
sam deploy \
  --stack-name classification-agents-dev \
  --capabilities CAPABILITY_IAM \
  --region us-east-1 \
  --resolve-s3 \
  --parameter-overrides \
      BudgetAlertEmail=you@example.com
```

| Parameter | Default | Description |
|---|---|---|
| `Taxonomy` | `law_enforcement` | Classification profile. Add values here when you add profiles. |
| `ReviewThreshold` | `0.60` | Results below this confidence get `needs_review = true`. |
| `MonthlyCostBudgetUsd` | `500` | Monthly AWS Budgets ceiling. |
| `BudgetAlertEmail` | (blank) | Email for budget alerts. The budget is created only when this is set. We recommend setting it. |
| `AdotLayerArn` | (blank) | Override for the [ADOT Python Lambda layer](https://aws-otel.github.io/docs/getting-started/lambda/lambda-python). Leave blank to use the default layer for your Region. |
| `Environment` | `dev` | Tag value. |

The stack creates the input and output S3 buckets, EventBridge rule, splitter and processor Lambda functions, SQS queue and dead-letter queue, DynamoDB results table, Bedrock Guardrail, an AgentCore Gateway with the Web Search tool target and its service role, a KMS key, CloudWatch alarms, and an optional budget. The only endpoint it creates is the gateway, which accepts only IAM-signed (SigV4) requests from principals granted `bedrock-agentcore:InvokeGateway`. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for operations, monitoring, and troubleshooting.

## Use the deployed pipeline

Get the input bucket name:

```bash
aws cloudformation describe-stacks --stack-name classification-agents-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`InputBucketName`].OutputValue' --output text
```

Upload a CSV under the `inbox/` prefix. The EventBridge rule ignores objects outside that prefix.

```bash
aws s3 cp test/test_with_context.csv s3://<INPUT_BUCKET>/inbox/
```

The CSV needs a `charge_text` column; the optional columns `state`, `county`, `charge_code` (or `statute_code`), `offense_type`, and `date_of_offense` are passed to the agent as context. A single-column file is also accepted.

Query results:

```bash
TABLE=classification-agents-dev-results

# Recent results
aws dynamodb scan --table-name $TABLE --limit 10

# Results that need human review
aws dynamodb scan --table-name $TABLE \
  --filter-expression "needs_review = :t" \
  --expression-attribute-values '{":t":{"BOOL":true}}'

# All results for one code (13A, Aggravated Assault)
aws dynamodb query --table-name $TABLE --index-name code-index \
  --key-condition-expression "code = :code" \
  --expression-attribute-values '{":code":{"S":"13A"}}'
```

## Estimate throughput and cost

Use these formulas with values measured from a short pilot of about 100 representative records; CloudWatch traces provide the timings and token counts.

**Throughput.** Each processor invocation handles one SQS batch sequentially, and up to the reserved concurrency run in parallel (default 100).

```
records per minute  = reserved_concurrency x 60 / seconds_per_record
```

For example, at 16 seconds per record and concurrency 100: 100 x 60 / 16 = 375 records per minute, so 10,000 records take about 27 minutes.

**Tuning tip:** each processor invocation handles one batch, so keep `BATCH_SIZE x seconds_per_record` comfortably under the 900-second Lambda timeout. The default of 25 records takes about 400 seconds at 16 seconds per record. Lower `BATCH_SIZE` if your records take longer.

**Cost per record.**

```
cost per record = Lambda + Bedrock + search
Lambda  = memory_GB x seconds_per_record x price_per_GB_second
Bedrock = model_calls x (input_tokens x input_price + output_tokens x output_price)
search  = searches_per_record x search_price
```

- **Lambda:** the processor has 2 GB. At the US East (N. Virginia) x86 price of $0.0000166667 per GB-second, a 16-second record costs 2 x 16 x 0.0000166667 = about $0.00053, or about $5.33 per 10,000 records. Request charges are negligible (one request per batch of 50).
- **Bedrock:** the agent makes one model call per turn, and each tool use adds a turn that resends the conversation, so input tokens grow with the number of searches. Output tokens include the model's reasoning. Take measured token counts from the CloudWatch traces and prices from [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/).
- **Search:** Web Search on Amazon Bedrock AgentCore is priced per query ($7 per 1,000 queries at the time of writing; see [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)), plus AgentCore Gateway invocation charges. Count searches per record from the traces; the agent decides how many to run.

For reference, in a small test (eight sample charges, default settings) each classified record took two model calls, about 6,300 input and 800 output tokens in total, and one or two searches. Your figures will vary with your data and settings.

S3, SQS, DynamoDB, and CloudWatch costs are small relative to model inference for this workload, but include them in a full estimate. Set `BudgetAlertEmail` so a budget alarm catches unexpected spend.

## Adapt to your domain

Everything domain-specific lives in a `ClassificationProfile` in [lambda_functions/taxonomies/](lambda_functions/taxonomies/). The agent, guardrail, schema validation, citation verification, and pipeline are shared.

1. Copy [law_enforcement.py](lambda_functions/taxonomies/law_enforcement.py) to a new module, for example `transportation.py`.
2. Define the code table, a fallback code, the primary input field, the context fields (with any aliases), the output field names, and the prompt wording (expert role, research steps, guidance, search tool name and description).
3. Register the profile in `_load_profiles()` in [taxonomies/\_\_init\_\_.py](lambda_functions/taxonomies/__init__.py), and add it to the `Taxonomy` parameter's `AllowedValues` in `template.yaml`.
4. Add tests. [test/test_profiles.py](test/test_profiles.py) includes a minimal second profile that shows the pattern.

To use a different search provider, pass any client with a `search(query, max_results)` method that returns `{"results": [{"title", "url", "content"}]}` to `create_classification_agent`, and update how the processor constructs it. [web_search.py](lambda_functions/web_search.py) is the reference implementation. To use a controlled corpus instead of the open web, replace the search tool with a retrieval tool backed by [Amazon Bedrock Knowledge Bases](https://aws.amazon.com/bedrock/knowledge-bases/).

## Responsible use

Classification results can inform consequential decisions, so the sample is built for decision support with a person in the loop.

- **Keep a person in the loop.** A qualified reviewer must check classifications before they are used in any legal, law enforcement, or other consequential decision. The CLI and web UI show this notice with every result.
- **Route flagged records to review.** Records with `needs_review = true` (low confidence, fallback code, blocked input, or an error) must not be used until a person has reviewed them. The [needs-review query](#use-the-deployed-pipeline) lists them; in production, send them to a review queue and record the reviewer's decision alongside the original result.
- **Audit with the evidence.** Each result keeps the reasoning and the verified source URLs, so a reviewer can check what the classification was based on.
- **Evaluate for bias before production.** Measure accuracy on a labeled sample from your jurisdictions, and check for disparate error rates across jurisdictions, charge types, and any demographic attributes your governance process requires. Charge descriptions can encode local practices, so a model that performs well in one jurisdiction may not in another. Repeat the evaluation when you change the model, prompt, or profile.
- **Screen and minimize inputs.** Keep the Bedrock Guardrail enabled, pass only the fields classification needs, and do not include names or other identifiers in the input.
- **Follow your governance requirements.** Uses that affect individuals may be subject to legal, policy, and oversight requirements in your jurisdiction. Review them before deploying.

## Security considerations

The input text is untrusted, and the controls below assume it may contain prompt-injection attempts.

- **Input screening:** an Amazon Bedrock Guardrail evaluates only the untrusted primary field (with `ApplyGuardrail`) before the model is invoked. Input blocked as a prompt attack returns the fallback code with `needs_review = true`. Names, addresses, US Social Security numbers, and driver IDs are anonymized, and the model receives the masked text. The original input is still stored in DynamoDB, so treat the results table as sensitive.
- **Prompt isolation:** every field is length-limited and wrapped in delimiters that the system prompt identifies as data, not instructions.
- **Output validation:** the model's JSON is validated against a strict schema. Unknown codes and out-of-range confidence values are rejected, the model gets one retry, and then the result falls back to the profile's fallback code. Malformed output is never written to DynamoDB.
- **Verified citations:** cited URLs are kept only if the search tool returned them for that record.
- **Logging:** the processor logs record IDs, never input text. CloudWatch Logs data protection policies mask PII at ingest.
- **Cost controls:** the EventBridge rule filters by prefix and object size, the splitter caps rows per upload, the processor has a reserved concurrency limit, and an optional AWS Budgets alarm is included.
- **Data protection:** S3 buckets block public access and deny non-TLS requests. Data at rest is encrypted with a customer managed KMS key.
- **Web UI:** the Streamlit app uses a shared password and is for local use only. For anything beyond local use, put it behind proper authentication, such as Amazon Cognito.
- **Web search:** queries composed by the agent are served by Web Search on Amazon Bedrock AgentCore within AWS. The gateway accepts only IAM-signed requests, and the processor's role can invoke only this stack's gateway.
- **Trace data:** Strands records the full prompt and response in OpenTelemetry trace events, which CloudWatch stores in the account-level `aws/spans` log group. The guardrail masks names, addresses, and ID numbers before the prompt is built, but the rest of the input text appears in traces. Restrict access to that log group. If traces must not contain input data, remove the ADOT layer, `AWS_LAMBDA_EXEC_WRAPPER`, and the `OTEL_*` variables from the processor in `template.yaml`, and set `AGENT_OBSERVABILITY_ENABLED` to `false`.
- **Search result terms:** the Web Search tool's acceptable use terms require you to retain and display the source citations for any search results you surface to end users (the `sources` field provides them), and prohibit storing or reproducing search result content in bulk. This sample stores classifications and citation URLs, not search result text.

The IAM policy allows invoking Anthropic Claude models on Amazon Bedrock. Scope it to the specific model or inference profile you use for production.

## Clean up

The stack's S3 buckets are versioned, so the stack cannot be deleted until every object version is removed.

```bash
STACK=classification-agents-dev
for OUT in InputBucketName OutputBucketName AccessLogsBucketName; do
  BUCKET=$(aws cloudformation describe-stacks --stack-name $STACK \
    --query "Stacks[0].Outputs[?OutputKey=='$OUT'].OutputValue" --output text)
  python3 -c "import boto3,sys; boto3.resource('s3').Bucket(sys.argv[1]).object_versions.delete()" "$BUCKET"
done
sam delete --stack-name $STACK
```

Deleting the stack deletes the DynamoDB results table and schedules the KMS key for deletion. Export any results you want to keep first.

## Project structure

```
lambda_functions/
  classifier_agent.py        Agent, schema validation, citation verification, review flag
  agent_prompt.py            System prompt, generated from the active profile
  taxonomies/
    __init__.py              ClassificationProfile and profile registry
    law_enforcement.py       NIBRS codes and profile
  lambda_splitter.py         S3 CSV to SQS batches
  lambda_processor.py        SQS batches to DynamoDB results
strands_agent_classifier.py  Local CLI
streamlit_ui.py              Local web UI
template.yaml                AWS SAM template
test/                        Unit tests
docs/
  DEPLOYMENT.md              Operations, monitoring, troubleshooting
  architecture/              Architecture diagram (editable .drawio source and SVG)
```

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
