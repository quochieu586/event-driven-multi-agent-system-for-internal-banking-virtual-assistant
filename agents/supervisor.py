import boto3
import json
import os

# Initialize the Bedrock Agent Runtime client
bedrock_runtime = boto3.client('bedrock-runtime')
lambda_client = boto3.client('lambda')

LLM_ARN = os.getenv("LLM_ARN", "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0") # Or your hardcoded ARN
AGENT_MAP = {
        "CUSTOMER": os.environ.get('CUSTOMER_AGENT_ARN'),
        "FINANCE": os.environ.get('FINANCE_AGENT_ARN'),
        "IT": os.environ.get('IT_AGENT_ARN')
    }

def get_prompt(user_query):
    return f"""
    You are the High-Level Supervisor for a Banking Multi-Agent System. 
    Your sole task is to analyze a user's request and determine which specialized department should handle it.

    **DEPARTMENTS**:
    1. CUSTOMER: Database actions on customer data (balances, onboarding, updates), account tiers (SME/Platinum), onboarding docs (ASL/NAICS), Auto-Sweep, lost cards, and address changes.
    2. FINANCE: Credit lines (CCL), SOFR rates, expense/travel policy, VAT filing, and audit thresholds ($50k+).
    3. IT_SUPPORT: Hardware requests, VPN/Okta access, phishing/security incidents, and software SOC2 reviews.
    4. GENERAL: Phatic communication (greetings, closings, polite filler).
    5. OTHERS: Non-banking inquiries (education, weather, sports, general knowledge).

    **RULES**:
    - Provide your reasoning in one short sentence.
    - Output the final decision as a single word: CUSTOMER, FINANCE, IT_SUPPORT, GENERAL, or OTHERS.

    **EXAMPLES**:
    User: "What documents do I need to open an SME account?"
    JSON: {{"reasoning": "Onboarding requirements for SME entities fall under Corporate Customer segments.", "decision": "CUSTOMER"}}

    User: "I clicked a weird link in my email and now my laptop is acting slow."
    JSON: {{"reasoning": "Potential phishing and hardware performance are cybersecurity/IT issues.", "decision": "IT_SUPPORT"}}

    User: "How much can I spend on a hotel in London for my business trip?"
    JSON: {{"reasoning": "Travel per diem and expense policies are managed by Finance Operations.", "decision": "FINANCE"}}

    USER_QUERY: "{user_query}"
    """

def get_intent(user_query):
    prompt = get_prompt(user_query)
    response = bedrock_runtime.invoke_model(
        modelId=LLM_ARN, # Changed to modelId
        contentType='application/json',
        accept='application/json',
        body=json.dumps({
            "inferenceConfig": {
                "max_new_tokens": 500,
                "temperature": 0.0
            },
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt}]
                }
            ]
        })
    )

    # 1. Read the StreamingBody
    response_raw = response['body'].read()
    
    # 2. Convert bytes to a Python dictionary
    response_data = json.loads(response_raw)

    # 3. Navigate the Nova-specific JSON structure
    # This is where your 'output' key actually lives!
    raw_text = response_data['output']['message']['content'][0]['text']

    try:
        # Locate the actual JSON boundaries to ignore prefixes like "JSON: " or "Here is it:"
        start_index = raw_text.find('{')
        end_index = raw_text.rfind('}')
        
        if start_index == -1 or end_index == -1:
            raise ValueError("No JSON brackets found in response")

        clean_json_str = raw_text[start_index:end_index + 1]
        result_dict = json.loads(clean_json_str)
        
        # Extract the decision
        decision = result_dict.get("decision", "OTHERS").strip().upper()
        
        # Map 'IT_SUPPORT' back to 'IT' if the model uses your example's naming
        if "IT" in decision: return "IT"
        
        return decision

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        # Fallback: Simple keyword search if parsing fails
        print(f"Extraction failed: {e}. Attempting keyword fallback.")
        upper_text = raw_text.upper()
        for option in ["CUSTOMER", "FINANCE", "IT_SUPPORT", "GENERAL", "OTHERS"]:
            if option in upper_text:
                return option
        return "OTHERS"

def handler(event, context):
    query_params = event.get('queryStringParameters', {})
    user_query = query_params.get('q', '').strip()
    thought_log = ""

    if not user_query:
        thought_log += f"\n• Supervisor could not find a query in the request."
        return {
            "statusCode": 400,
            "message": "Failed: No query provided",
            "body": {
                "agent": None,
                "action": "GENERAL_RESPONSE",
                "data": {},
                "display_text": "Sorry, I can't get your question. This may be a system issue. Please try again later or contact support if the issue persists.",
                "thought_log": thought_log
            }
        }

    # 1. Identify intent
    thought_log += f"\n• Supervisor is analyzing the query to determine the appropriate department..."
    intent = get_intent(user_query)
    thought_log += f"\n• Supervisor determined the intent to be: {intent}"

    agent_map = {
        "CUSTOMER": os.environ.get('CUSTOMER_AGENT_ARN'),
        "FINANCE": os.environ.get('FINANCE_AGENT_ARN'),
        "IT": os.environ.get('IT_AGENT_ARN')
    }

    # 2. Routing Logic
    if intent in agent_map:
        thought_log += f"\n• Supervisor is routing the query to the {intent} Agent..."
        target_arn = agent_map[intent]
        
        # Invoke specialized agent
        response = lambda_client.invoke(
            FunctionName=target_arn,
            InvocationType='RequestResponse',
            Payload=json.dumps({"queryStringParameters": {"q": user_query, "thought_log": thought_log}})
        )

        # 3. CRITICAL: "Drain" the stream and parse it
        response_payload = json.loads(response['Payload'].read().decode('utf-8'))

        # 4. Return the actual data back to the User/FE
        return response_payload

    # 3. Handle non-agent intents (General/Others)
    else:
        thought_log += f"\n• Supervisor is generating a general response..."
        if intent == "GENERAL":
            answer = "Hello! I'm your banking assistant. How can I help with your accounts, finance, or IT needs?"
        else:
            answer = "I'm sorry, I'm a specialized banking assistant and don't have information on that topic."
        
        thought_log += f"\n• Supervisor finished generating the response."

        return {
            'statusCode': 200,
            'message': 'Success',
            'body': {
                'agent': None,
                'action': 'GENERAL_RESPONSE',
                'data': {},
                'display_text': answer,
                'thought_log': thought_log
            }
        }