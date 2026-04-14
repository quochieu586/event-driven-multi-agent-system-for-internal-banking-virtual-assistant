import boto3
import json
import os
import gspread
import google.auth

# Initialize the Bedrock Agent Runtime client
bedrock_agent_runtime = boto3.client('bedrock-agent-runtime')
bedrock_runtime = boto3.client('bedrock-runtime')

CUSTOMER_KB_ID = os.environ.get("KNOWLEDGE_BASE_ID") # Or your hardcoded ID
LLM_ARN = os.environ.get("LLM_ARN") # Or your hardcoded ARN

PARAM_TYPE = dict[str, int | str | float | bool]
COL_MAPPING = {         # hardcoded mapping of column names to indices (1-based for gspread)
    "customer_id": 1,
    "full_name": 2,
    "category": 3,
    "account_tier": 4,
    "balance": 5,
    "status": 6,
    "kyc_verified": 7,
    "notes": 8
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets", 
    "https://www.googleapis.com/auth/cloud-platform"
]

class GSheetManager:
    def __init__(self, spreadsheet_id, sheet_name="customer"):
        creds, _ = google.auth.default(scopes=SCOPES)
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(spreadsheet_id)
        self.worksheet = self.sh.worksheet(sheet_name)

    def get_customer(self, details_json: PARAM_TYPE) -> list[PARAM_TYPE]:
        """Finds a customer by ID or Name."""
        customer_id = details_json.get("customer_id", None)
        full_name = details_json.get("full_name", None)

        if not customer_id and not full_name:
            return {
                "statusCode": 400,
                "message": "Failed: Missing identifier. Please provide either a customer_id or full_name to search for.",
                "body": {
                    "agent": "CUSTOMER",
                    "action": "GET_INFO",
                    "data": [],
                    "display_text": "Please provide either a customer_id or full_name to search for."
                }
            }
        
        customer_id = str(customer_id).strip().lower() if customer_id else ""
        full_name = str(full_name).strip().lower() if full_name else ""

        records = self.worksheet.get_all_records()
        results = []
        requested_fields = details_json.get("requested_fields", COL_MAPPING.keys())  # If not specified, return all fields
        
        for row in records:
            row_cust_name = str(row.get("full_name")).strip().lower()
            row_cust_id = str(row.get("customer_id")).strip().lower()

            # Check both customer_id and name columns
            ## for cust_name => check substring match to allow for partial name queries, but for cust_id do exact match to avoid false positives
            if (customer_id == row_cust_id) or (full_name in row_cust_name ):
                # Filter the row to only include requested fields
                filtered_row = {
                    "customer_id": row.get("customer_id"),
                    "full_name": row.get("full_name")
                }
                for field in requested_fields:
                    if field in row and field not in ["customer_id", "full_name"]:  # we always include ID and Name for context
                        filtered_row[field] = row.get(field)

                results.append(filtered_row)
        
        if len(results) == 0:
            display_text = f"No matching customers found for customer {customer_id or full_name}."
        else:
            display_text = f"Found {len(results)} matching customer(s)."

        return {
            "statusCode": 200,
            "message": f"Success",
            "body": {
                "agent": "CUSTOMER",
                "action": "GET_INFO",
                "data": results,
                "display_text": display_text
            }
        }

    def add_customer(
            self,
            details_json: PARAM_TYPE,
        ):
        """Appends a new customer row with default values."""
        # Adjust these indices based on your actual column order
        # get current max customer_id and increment for new ID
        if not details_json.get("full_name", None):
            return {
                "statusCode": 400,
                "message": "Failed: full_name is required.",
                "body": {
                    "agent": "CUSTOMER",
                    "action": "ADD_CUSTOMER",
                    "data": [],
                    "display_text": "Please provide a name to add a new customer."
                }
            }

        all_records = self.worksheet.get_all_records()
        existing_ids = [int(row["customer_id"].split("-")[1]) for row in all_records]
        new_id_num = max(existing_ids) + 1 if existing_ids else 1
        customer_id = f"CUST-{new_id_num:03d}"

        next_row_index = len(all_records) + 2   # for appending at the end of the sheet
        print(f"next row index for new customer: {next_row_index}")

        new_row = [
            customer_id, 
            details_json.get("full_name"),
            details_json.get("category", "Retail"),
            details_json.get("account_tier", "Standard"),
            details_json.get("balance", 0),
            details_json.get("status", "Active"),
            details_json.get("kyc_verified", "FALSE"),
            details_json.get("notes", "")
        ]
        self.worksheet.insert_row(
            new_row, 
            index=next_row_index, 
            value_input_option='USER_ENTERED'
        )

        return {
            "statusCode": 200,
            "message": f"Success",
            "body": {
                "agent": "CUSTOMER",
                "action": "ADD_CUSTOMER",
                "data": [
                    {
                        key: value for key, value in zip(COL_MAPPING.keys(), new_row)
                    }
                ],
                "display_text": f"I have successfully added new customer {details_json.get('full_name')} with ID {customer_id}."
            }
        }

    def update_balance(self, details_json: PARAM_TYPE):
        """Increases or decreases balance by a specific amount."""
        customer_id = details_json.get("customer_id", None)
        customer_name = details_json.get("full_name", None)
        amount = details_json.get("amount", None)

        if (not customer_id and not customer_name) or amount is None:
            return {
                "statusCode": 400,
                "message": "Failed: customer_id or full_name and amount are required to update balance",
                "body": {
                    "agent": "CUSTOMER",
                    "action": "UPDATE_BALANCE",
                    "data": [],
                    "display_text": "Please provide a customer ID or name and an amount to update the balance."
                }
            }

        # 1. Find the cell containing the ID or Name
        cell = self.worksheet.find(str(details_json.get("customer_id")))
        if not cell:
            cell = self.worksheet.find(str(details_json.get("full_name")))
        if not cell:
            return {
                "statusCode": 404,
                "message": "Failed: Customer not found",
                "body": {
                    "agent": "CUSTOMER",
                    "action": "UPDATE_BALANCE",
                    "data": [],
                    "display_text": f"Customer {customer_name or customer_id} not found."
                }
            }

        # 2. Assume 'balance' is in Column 4 (D) - change index if different!
        balance_col_id = COL_MAPPING["balance"]
        current_balance_str = self.worksheet.cell(cell.row, balance_col_id).value or "0"
        current_balance_str = current_balance_str.replace(',', '.').strip()
        current_balance = float(current_balance_str)
        new_balance = current_balance + amount

        # if negative new balance, reject the transaction
        if new_balance < 0:
            return {
                "statusCode": 400,
                "message": "Failed: Insufficient funds. Balance cannot be negative.",
                "body": {
                    "agent": "CUSTOMER",
                    "action": "UPDATE_BALANCE",
                    "data": [],
                    "display_text": f"Transaction failed. Current balance ({current_balance}) is insufficient for the requested amount."
                }
            }

        name_col_id = COL_MAPPING["full_name"]
        customer_name = self.worksheet.cell(cell.row, name_col_id).value

        customer_id_col_id = COL_MAPPING["customer_id"]
        customer_id = self.worksheet.cell(cell.row, customer_id_col_id).value

        # 3. Update the specific cell
        self.worksheet.update_cell(cell.row, balance_col_id, new_balance)
        
        return {
            "statusCode": 200,
            "message": f"Success",
            "body": {
                "agent": "CUSTOMER",
                "action": "UPDATE_BALANCE",
                "data":
                    [
                        {
                            "customer_id": customer_id,
                            "full_name": customer_name,
                            "old_balance": current_balance,
                            "new_balance": new_balance
                        }
                    ],
                "display_text": f"Balance for customer {customer_name} (ID: {customer_id}) updated to {new_balance}."
            }
        }

    def update_customer_info(self, details_json: PARAM_TYPE):
        customer_id = details_json.get("customer_id", None)
        customer_name = details_json.get("full_name", None)
        requested_updates = {key: value for key, value in details_json.items() if key not in ["customer_id", "full_name"] and key in COL_MAPPING}

        if not customer_id and not customer_name:
            return {
                "statusCode": 400,
                "message": "Failed: customer_id or full_name is required to update customer info",
                "body": {
                    "agent": "CUSTOMER",
                    "action": "UPDATE_INFO",
                    "data": [],
                    "display_text": "Please provide a customer ID or name to update their information."
                }
            }
        
        if not requested_updates:
            return {
                "statusCode": 400,
                "message": f"Failed: No valid fields to update. Allowed fields are {', '.join(COL_MAPPING.keys())}.",
                "body": {
                    "agent": "CUSTOMER",
                    "action": "UPDATE_INFO",
                    "data": [],
                    "display_text": "Please specify valid fields to update (category, account_tier, status, kyc_verified, notes)."
                }
            }

        # 1. Find the cell containing the ID or Name
        cell = self.worksheet.find(str(details_json.get("customer_id")))
        if not cell:
            cell = self.worksheet.find(str(details_json.get("full_name")))
        if not cell:
            return {
                "statusCode": 404,
                "message": "Failed: Customer not found",
                "body": {
                    "agent": "CUSTOMER",
                    "action": "UPDATE_INFO",
                    "data": [],
                    "display_text": f"Customer {details_json.get('customer_id') or details_json.get('full_name')} not found."
                }
            }

        # 2. Update the specific cells based on requested_updates
        for field, new_value in requested_updates.items():
            col_id = COL_MAPPING[field]
            self.worksheet.update_cell(cell.row, col_id, new_value)
        
        return {
            "statusCode": 200,
            "message": f"Success",
            "body": {
                "agent": "CUSTOMER",
                "action": "UPDATE_INFO",
                "data": [
                    {
                        "customer_id": self.worksheet.cell(cell.row, COL_MAPPING["customer_id"]).value,
                        "full_name": self.worksheet.cell(cell.row, COL_MAPPING["full_name"]).value,
                        **{field: self.worksheet.cell(cell.row, COL_MAPPING[field]).value for field in requested_updates.keys()}
                    }
                ],
                "display_text": f"Customer information updated successfully."
            }
        }



def invoke_model_with_prompt(prompt: str) -> dict:
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

    return response


def extract_json_payload(raw_text: str) -> dict:
    """
    Extracts the JSON payload from the raw text response of the LLM. This is necessary because the LLM might include explanations or prefixes before the actual JSON.
    """
    start_index = raw_text.find('{')
    end_index = raw_text.rfind('}')
    
    if start_index == -1 or end_index == -1:
        raise ValueError("No JSON brackets found in response")

    clean_json_str = raw_text[start_index:end_index + 1]
    return json.loads(clean_json_str)


def get_llm_decision(user_query: str) -> str:
    """
    Recognizes the action needed. Returns one of:
    1. "GET_INFO" - If the request is about retrieving information on a customer
    2. "ADD_NEW_USER" - If the request is about adding a new customer
    3. "UPDATE_BALANCE" - If the request is about updating a customer's balance
    4. "IMPOSSIBLE_ACTION" - If the request is impossible to answer or doesn't fit the above categories
    5. "NONE" - If the request is a general question about customer-related information, including account tiers (SME/Platinum), onboarding docs (ASL, NAICS), Auto-Sweep, lost cards, and address changes.
    """
    prompt = f"""
    You are the **Banking Intent Router**. Your sole purpose is to classify the user's intent into a specific technical action code.

    **ACTION CODES:**
    1. `GET_INFO`: Use this if the user wants to check a balance, verify KYC status, or retrieve any details about an existing customer's specific record.
    2. `ADD_NEW_USER`: Use this if the user wants to create, open, or onboard a new customer account.
    3. `UPDATE_INFO`: Use this if the user wants to change any non-balance information about an existing customer, such as their name, category, account tier, status, KYC verification, or notes.
    4. `UPDATE_BALANCE`: Use this if the user wants to deposit, withdraw, or change a numeric balance.
    5. `IMPOSSIBLE_ACTION`: Use this if the user asks for a database change we do NOT support (e.g., "Delete a user" or "Change a phone number").
    6. `KNOWLEDGE_QUERY`: Use this for policy, procedure, or general product questions that require searching the Knowledge Base. This includes:
        - Account Tiers: SME vs. Platinum benefits and requirements.
        - Onboarding Documentation: Questions about ASL (Authorized Signatory List) or NAICS codes.
        - Banking Services: Auto-Sweep functionality or reporting Lost Cards.
        - Administrative Procedures: How to perform an Address Change or what documents are needed for it.
    
    **EXAMPLES:**
    User: "What is the balance of customer CUST-001?"
    Response: GET_INFO

    User: "Add a new customer named John Doe in the Retail category with a Standard account tier."
    Response: ADD_NEW_USER

    User: "Withdraw $500 from customer CUST-002's account."
    Response: UPDATE_BALANCE

    User: "Update the status of customer CUST-003 to 'Inactive'."
    Response: UPDATE_INFO

    User: "Deactivate the account of customer named Jane Smith."
    Response: UPDATE_INFO

    User: "Leverage the customer named Faker to be the boss."
    Response: IMPOSSIBLE_ACTION
    
    User: "What are the benefits of a Platinum account tier?"
    Response: KNOWLEDGE_QUERY

    **RULES:**    
    - Respond with ONLY the ACTION CODE, no explanations or additional text.

    USER_QUERY: "{user_query}"
    """
    response = invoke_model_with_prompt(prompt)

    # 1. Read the StreamingBody
    response_raw = response['body'].read()
    
    # 2. Convert bytes to a Python dictionary
    response_data = json.loads(response_raw)
    return response_data['output']['message']['content'][0]['text']


def extract_action_details(user_query, action_code: str) -> dict:
    """
    Extracts necessary details from the user query based on the action code in case the database action is necessary.
    """
    # For simplicity, this is a placeholder. In production, you'd implement robust parsing or use another LLM call.
    prompt = f"""
    You are a Data Extraction Specialist for a Banking API. Your job is to extract entities from a user's query and format them into a strict JSON object based on the provided ACTION_CODE.

    **EXTRACTION RULES:**
    1. For action code GET_INFO:
        - If the user provides an ID, use the key: "customer_id"
        - If the user provides a name, use the key: "full_name"
        - Extract a list of requested columns into the key: "requested_fields"
        - Allowed fields: ["category", "account_tier", "balance", "status", "kyc_verified", "notes"]
        - If the user doesn't specify requested fields, assume they want all (allowed) fields.
    2. For action code ADD_NEW_USER:
        - Provide a flat JSON structure
        - Required key: "full_name".
        - Optional keys (extract only if mentioned): "category", "account_tier", "balance", "status", "kyc_verified", "notes"
    3. For action code UPDATE_BALANCE:
        - If the user provides an ID, use the key: "customer_id"
        - If the user provides a name, use the key: "full_name"
        - Required key: "amount"
        - Math Rule: Use a positive number for deposits/increases and a negative number for withdrawals/decreases.
    4. For action code UPDATE_INFO:
        - If the user provides an ID, use the key: "customer_id"
        - If the user provides a name, use the key: "full_name"
        - Extract the specific fields they want to update with their new values. Allowed fields are the same as GET_INFO.

    **EXAMPLES:**
    User: "What is the balance and KYC status for CUST-009?"
    JSON: {{"customer_id": "CUST-009", "requested_fields": ["balance", "kyc_verified"]}}

    User: "What is the balance and tier for customer named 'Tran Quoc Hieu'?"
    JSON: {{"full_name": "Tran Quoc Hieu", "requested_fields": ["balance", "account_tier"]}}

    User: "Register Sarah Jenkins as a Platinum member with a 5000 balance."
    JSON: {{"full_name": "Sarah Jenkins", "account_tier": "Platinum", "balance": 5000}}

    User: "Withdraw 200 from the account of Tran Quoc Hieu."
    JSON: {{"full_name": "Tran Quoc Hieu", "amount": -200}}

    User: "Deposit 150 into customer ID 8821."
    JSON: {{"customer_id": "8821", "amount": 150}}

    User: "Change the account tier of Sarah Jenkins to Standard."
    JSON: {{"full_name": "Sarah Jenkins", "account_tier": "Standard"}}

    User: "Update the status of customer CUST-003 to 'Inactive'."
    JSON: {{"customer_id": "CUST-003", "status": "Inactive"}}

    User: "Deactivate the account of Tran Quoc Hieu."
    JSON: {{"full_name": "Tran Quoc Hieu", "status": "Inactive"}}

    **RULES:**
    - Respond with ONLY the JSON object, no explanations or additional text.
    - If an identifier is missing, use null.
    
    USER_QUERY: "{user_query}"
    ACTION_CODE: "{action_code}"
    """
    
    response = invoke_model_with_prompt(prompt)

    # 1. Read the StreamingBody
    response_raw = response['body'].read()

    # 2. Convert bytes to a Python dictionary
    response_data = json.loads(response_raw)
    raw_text = response_data['output']['message']['content'][0]['text']

    return extract_json_payload(raw_text)

def handler(event, context):
    query_params = event.get('queryStringParameters', {})
    user_query = query_params.get('q', None)
    thought_log = query_params.get('thought_log', '')

    thought_log += "\n• Customer Agent is executing the query..."

    if not user_query:
        thought_log += "\n• Customer Agent did not receive a query."
        return {
            "statusCode": 400,
            "message": "Failed: No query provided.",
            "body": {
                "agent": "CUSTOMER",
                "action": "NONE",
                "data": [],
                "display_text": "Sorry, I can't get your question. This may be a system issue. Please try again later or contact support if the issue persists.",
                "thought_log": thought_log
            }
        }
    try:
        thought_log += "\n• Customer Agent is determining the required action for the query..."
        action_code = get_llm_decision(user_query)
        thought_log += f"\n• Customer Agent determined action code: {action_code}"

        details_json = {}
        # Case database action is needed
        if action_code in ["GET_INFO", "ADD_NEW_USER", "UPDATE_BALANCE", "UPDATE_INFO"]:
            spreadsheet_id = "1ONF1oTXfPhY3JXVRbmvi934d2eOXeZQq3cjbx7-Rw-I"
            manager = GSheetManager(spreadsheet_id)

            thought_log += f"\n• Customer Agent is extracting details for action {action_code}..."
            details_json = extract_action_details(user_query, action_code)

            response = {}
            if action_code == "GET_INFO":
                thought_log += f"\n• Customer Agent is retrieving customer information..."
                response = manager.get_customer(details_json)
                if response['statusCode'] == 200:
                    thought_log += f"\n• Customer Agent successfully retrieved the information."
                else:
                    thought_log += f"\n• Customer Agent failed to retrieve the information."

            elif action_code == "ADD_NEW_USER":
                thought_log += f"\n• Customer Agent is adding a new customer..."
                response = manager.add_customer(details_json)
                if response['statusCode'] == 200:
                    thought_log += f"\n• Customer Agent successfully added the new customer."
                else:
                    thought_log += f"\n• Customer Agent failed to add the new customer."

            elif action_code == "UPDATE_BALANCE":
                thought_log += f"\n• Customer Agent is updating customer balance..."
                response = manager.update_balance(details_json)
                if response['statusCode'] == 200:
                    thought_log += f"\n• Customer Agent successfully updated the balance."
                else:
                    thought_log += f"\n• Customer Agent failed to update the balance."

            elif action_code == "UPDATE_INFO":
                thought_log += f"\n• Customer Agent is updating customer information..."
                response = manager.update_customer_info(details_json)
                if response['statusCode'] == 200:
                    thought_log += f"\n• Customer Agent successfully updated the information."
                else:
                    thought_log += f"\n• Customer Agent failed to update the information."

            response['body']['thought_log'] = thought_log
            return response

        # Case impossible action
        if action_code == "IMPOSSIBLE_ACTION":
            return {
                "statusCode": 400,
                "message": "Failed: The requested action is not supported by the API.",
                "body": {
                    "agent": "CUSTOMER",
                    "action": action_code,
                    "data": [],
                    "display_text": "The requested action is not supported by the system.",
                    "thought_log": thought_log
                }
            }

        # try:
        # 2. Ask the Knowledge Base
        # This searches S3 and uses an LLM to generate the answer
        if action_code == "KNOWLEDGE_QUERY":
            thought_log += f"\n• Customer Agent is retrieving relevant information..."
            response = bedrock_agent_runtime.retrieve_and_generate(
                input={'text': user_query},
                retrieveAndGenerateConfiguration={
                    'type': 'KNOWLEDGE_BASE',
                    'knowledgeBaseConfiguration': {
                        'knowledgeBaseId': CUSTOMER_KB_ID,
                        'modelArn': LLM_ARN
                    }
                }
            )

            output_text = response['output']['text']
            # Extract citations so the user knows where the info came from
            citations = response.get('citations', [])
            thought_log += f"\n• Customer Agent finished generating answer."

            return {
                'statusCode': 200,
                'message': f'Success',
                'body': {
                    "agent": "CUSTOMER",
                    "action": action_code,
                    "data": {},
                    "display_text": output_text,
                    "thought_log": thought_log
                }
            }

        else:
            thought_log += f"\n• Customer Agent could not recognize the intent of the query."
            return {
                "statusCode": 400,
                "message": "Failed: Could not recognize the intent of the query.",
                "body": {
                    "agent": "CUSTOMER",
                    "action": action_code,
                    "data": [],
                    "display_text": "I could not understand your request. Please rephrase it or contact support for assistance.",
                    "thought_log": thought_log
                }
            }

    except Exception as e:
        # If it fails, send an error back that the Supervisor can parse
        thought_log += f"\n• Customer Agent encountered an error: {str(e)}"

        return {
            'statusCode': 500,
            'message': f'Failed: error {str(e)}',
            'body': {
                "agent": "CUSTOMER",
                "action": "NONE",
                "data": [],
                "display_text": "Sorry, I couldn't process your request due to a system error. Please try again later or contact support if the issue persists.",
                "thought_log": thought_log
            }
        }