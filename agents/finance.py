import boto3
import json
import os
import time

# Initialize the Bedrock Agent Runtime client
bedrock_agent_runtime = boto3.client('bedrock-agent-runtime')
dynamodb = boto3.resource('dynamodb')

FINANCE_KB_ID = os.environ.get("KNOWLEDGE_BASE_ID") # Or your hardcoded ID
LLM_ARN = os.environ.get("LLM_ARN") # Or your hardcoded ARN
TABLE_NAME = os.environ.get("TABLE_NAME") # Or your hardcoded table name

def handler(event, context):
    table = dynamodb.Table(TABLE_NAME)
    thought_log = ""

    # 1. SQS sends messages in a "Records" list
    for record in event['Records']:
        # Parse the message body sent by the Supervisor
        try:
            body = json.loads(record['body'])
        except Exception as e:
            print(f"Agent Finance fails to parse the record")
            continue
    
        # We need a RequestID to link the answer back to the user later
        request_id = body.get('request_id')
        user_query = body.get('q', None)


        if not user_query:
            # If for some reason we don't have a query, we should log this and skip processing
            thought_log += f"\n• Finance Agent received a message without a query. Skipping processing."
            table.put_item(
                Item={
                    'id': request_id,
                    'status': 'FAILED',
                    'action_code': 'GENERAL_RESPONSE',
                    'agent': 'FINANCE',
                    'error': 'No query provided in the message',
                    'display_text': "Sorry, I can't receive your query.",
                    'thought_log': thought_log,
                    'ttl': int(time.time()) + 60 * 2 # Set TTL to automatically clean up after 2 minutes
                }
            )
            continue

        thought_log += "\n• Finance Agent is executing the query..."

        try:
            thought_log += f"\n• Finance Agent is retrieving relevant information..."

            # 2. Ask the Knowledge Base (Same Bedrock logic)
            response = bedrock_agent_runtime.retrieve_and_generate(
                input={'text': user_query},
                retrieveAndGenerateConfiguration={
                    'type': 'KNOWLEDGE_BASE',
                    'knowledgeBaseConfiguration': {
                        'knowledgeBaseId': FINANCE_KB_ID,
                        'modelArn': LLM_ARN
                    }
                }
            )

            output_text = response['output']['text']
            thought_log += f"\n• Finance Agent finished generating answer."

            # 3. Instead of returning, we WRITE to DynamoDB
            table.put_item(
                Item={
                    'id': request_id,
                    'status': 'COMPLETED',
                    'agent': 'FINANCE',
                    'display_text': output_text,
                    'thought_log': thought_log,
                    'action_code': 'KNOWLEDGE_QUERY',
                    'ttl': int(time.time()) + 60 * 2 # Set TTL to automatically clean up after 2 minutes
                }
            )

        except Exception as e:
            thought_log += f"\n• Finance Agent encountered an error: {str(e)}"
            # Record the failure so the user isn't stuck "Waiting" forever
            table.put_item(
                Item={
                    'id': request_id,
                    'status': 'FAILED',
                    'agent': 'FINANCE',
                    'action_code': 'GENERAL_RESPONSE',
                    'error': str(e),
                    'display_text': "Sorry, I encountered a system issue.",
                    'thought_log': thought_log,
                    'ttl': int(time.time()) + 60 * 2 # Set TTL to automatically clean up after 2 minutes
                }
            )
            
    # SQS triggers require no return value to "succeed"
    return {"status": "processed"}