import boto3
import json
import os

# Initialize the Bedrock Agent Runtime client
bedrock_agent_runtime = boto3.client('bedrock-agent-runtime')
IT_KB_ID = os.environ.get("KNOWLEDGE_BASE_ID") # Or your hardcoded ID
LLM_ARN = os.environ.get("LLM_ARN") # Or your hardcoded ARN

def handler(event, context):
    # 1. Get the question from Streamlit
    query_params = event.get('queryStringParameters', {})
    user_query = query_params.get('q', 'What is the standard laptop for general staff?')
    thought_log = query_params.get('thought_log', '')  # This is a JSON string of the thought log

    thought_log += "\n• IT Agent is executing the query..."

    try:
        # 2. Ask the Knowledge Base
        # This searches S3 and uses an LLM to generate the answer
        thought_log += f"\n• IT Agent is retrieving relevant information..."

        response = bedrock_agent_runtime.retrieve_and_generate(
            input={'text': user_query},
            retrieveAndGenerateConfiguration={
                'type': 'KNOWLEDGE_BASE',
                'knowledgeBaseConfiguration': {
                    'knowledgeBaseId': IT_KB_ID,
                    'modelArn': LLM_ARN
                }
            }
        )

        output_text = response['output']['text']
        # Extract citations so the user knows where the info came from
        citations = response.get('citations', [])

        thought_log += f"\n• IT Agent finished generating answer."

        return {
                'statusCode': 200,
                'message': f'Success',
                'body': {
                    'agent': 'IT_SUPPORT',
                    'action': 'KNOWLEDGE_QUERY',
                    'data': {},
                    'display_text': output_text,
                    'thought_log': thought_log,
                }
            }

    except Exception as e:
        # If it fails, send an error back that the Supervisor can parse
        thought_log += f"\n• IT Agent encountered an error: {str(e)}"
        return {
            'statusCode': 500,
            'message': f'Failed: Error {str(e)}',
            'body': {
                'agent': 'IT_SUPPORT',
                'action': 'KNOWLEDGE_QUERY',
                'data': {},
                'display_text': "Sorry, I can't get your question. This may be a system issue. Please try again later or contact support if the issue persists.",
                'thought_log': thought_log
            }
        }